"""按章合并摘要/向量维护；慢调用之间不持有正文事务或旧 ORM 对象。"""
import hashlib
import logging
from dataclasses import dataclass

from sqlalchemy import LargeBinary, cast, exists, select, update

from ..models.novel import Chapter, ChapterOutline, ChapterVersion
from ..utils.json_utils import remove_think_tags
from .chapter_ingest_service import ChapterIngestionService
from .llm_service import LLMService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChapterSnapshot:
    id: int
    version_id: int
    content: str
    title: str

    @property
    def fingerprint(self):
        return self.version_id, hashlib.sha256(self.content.encode("utf-8")).digest(), self.title


class ChapterMaintenanceService:
    def __init__(self, runner):
        self.runner = runner
        self.pending = {}

    def schedule(self, project_id, chapter_number, user_id):
        key = (project_id, chapter_number)
        if key in self.pending:
            self.pending[key]["rerun"] = True
            self.pending[key]["user_id"] = user_id
            return
        state = {"rerun": True, "user_id": user_id}
        self.pending[key] = state
        task = self.runner.own(self._drain(key, state))
        if task is None:
            self.pending.pop(key, None)
        else:
            # 排队任务若在执行前取消，协程的 finally 不会运行。
            task.add_done_callback(lambda _: self._forget(key, state))

    def _forget(self, key, state):
        if self.pending.get(key) is state:
            self.pending.pop(key)

    async def _drain(self, key, state):
        try:
            while state["rerun"]:
                state["rerun"] = False
                try:
                    await self._refresh(*key, state)
                except Exception:
                    logger.exception("章节维护失败: project=%s chapter=%s", *key)
        finally:
            self._forget(key, state)

    async def _load(self, project_id, chapter_number):
        async with self.runner.session_factory() as session:
            row = (await session.execute(select(
                Chapter.id, Chapter.selected_version_id, ChapterVersion.content, ChapterOutline.title,
            ).join(ChapterVersion, ChapterVersion.id == Chapter.selected_version_id).outerjoin(
                ChapterOutline, (ChapterOutline.project_id == Chapter.project_id)
                & (ChapterOutline.chapter_number == Chapter.chapter_number),
            ).where(Chapter.project_id == project_id, Chapter.chapter_number == chapter_number))).first()
            if row is None:
                return None
            return ChapterSnapshot(row.id, row.selected_version_id, row.content or "", row.title or f"第{chapter_number}章")

    async def _save_summary(self, snapshot, summary):
        # 条件更新也覆盖“复查以后、执行 UPDATE 以前”正文被修改的窗口。
        # BINARY/BLOB 比较避免 MySQL 文本排序规则把大小写不同的正文视为相同。
        async with self.runner.session_factory() as session:
            current_version = exists(select(ChapterVersion.id).where(
                ChapterVersion.id == snapshot.version_id,
                cast(ChapterVersion.content, LargeBinary) == snapshot.content.encode("utf-8"),
            ))
            result = await session.execute(update(Chapter).where(
                Chapter.id == snapshot.id, Chapter.selected_version_id == snapshot.version_id, current_version,
            ).values(real_summary=summary).execution_options(synchronize_session=False))
            await session.commit()
            return result.rowcount == 1

    async def _refresh(self, project_id, chapter_number, state):
        snapshot = await self._load(project_id, chapter_number)
        if snapshot is None:
            return

        async def is_current():
            current = await self._load(project_id, chapter_number)
            if current and current.fingerprint == snapshot.fingerprint:
                return True
            if current:
                state["rerun"] = True
            return False

        async with self.runner.session_factory() as session:
            llm = LLMService(session)
            summary = None
            try:
                if snapshot.content.strip():
                    summary = remove_think_tags(await llm.get_summary(
                        snapshot.content, temperature=0.15, user_id=state["user_id"],
                    ))
            except Exception:
                logger.exception("章节摘要维护失败: project=%s chapter=%s", project_id, chapter_number)
            await session.rollback()
            if not await is_current():
                return
            if summary and not await self._save_summary(snapshot, summary):
                await is_current()
                return
            await ChapterIngestionService(llm_service=llm).ingest_chapter(
                project_id=project_id, chapter_number=chapter_number, title=snapshot.title,
                content=snapshot.content, summary=summary, user_id=state["user_id"], is_current=is_current,
            )
            # 最后一次向量写入已经在途时也可能有新正文，完成后再检查并合并重跑。
            await is_current()
