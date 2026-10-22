"""剩余 AI 操作复用持久任务生命周期；每次执行使用独立的数据库会话。"""
from io import BytesIO
from types import SimpleNamespace

from fastapi import UploadFile
from sqlalchemy import select

from ..models.novel import Chapter
from ..schemas.novel import EvaluateChapterRequest, GenerateOutlineRequest

LONG_OPERATION_KINDS = (
    "chapter_evaluate", "outline_extend", "chapter_optimize", "novel_import",
    "emotion_analyze", "six_dimension_review", "consistency_review", "concept_converse",
)


class LongOperationService:
    def __init__(self, kind):
        self.kind = kind

    async def run(self, context):
        # 延迟导入避免路由装配与任务注册形成循环依赖。
        from ..api.routers import analytics, optimizer, review, writer
        from .import_service import ImportService

        await context.advance("preparing", 10)
        async with context.session_factory() as session:
            user = SimpleNamespace(id=context.user_id)
            payload = context.payload
            await context.advance(self.kind, 30)
            if self.kind == "novel_import":
                file = UploadFile(BytesIO(payload["content"].encode("utf-8")), filename=payload["filename"])
                try:
                    project_id = await ImportService(session).import_novel_from_file(
                        context.user_id, file, project_id=context.project_id, progress=context.advance,
                    )
                    result = {"id": project_id}
                finally:
                    await file.close()
            elif self.kind == "concept_converse":
                from .concept_service import ConceptService
                from ..schemas.novel import ConverseRequest
                result = await ConceptService(session).converse(context.project_id, context.user_id, ConverseRequest(**payload))
            elif self.kind == "chapter_evaluate":
                context.previous_chapter_status = await session.scalar(select(Chapter.status).where(
                    Chapter.project_id == context.project_id, Chapter.chapter_number == payload["chapter_number"],
                ))
                await session.rollback()
                result = await writer._execute_evaluate_chapter(context.project_id, EvaluateChapterRequest(**payload), session, user)
            elif self.kind == "outline_extend":
                result = await writer._execute_generate_chapters_outline(context.project_id, GenerateOutlineRequest(**payload), session, user, context.runner)
            elif self.kind == "chapter_optimize":
                result = await optimizer._execute_optimize_chapter(optimizer.OptimizeRequest(**payload), session, user)
            elif self.kind == "emotion_analyze":
                result = await analytics._execute_analyze_emotion_with_ai(context.project_id, session, user)
            elif self.kind == "six_dimension_review":
                result = await review._execute_review_six_dimension(review.SixDimensionReviewRequest(**payload), session, user)
            else:
                result = await review._execute_review_consistency(review.ConsistencyReviewRequest(**payload), session, user)
        await context.advance("persisted", 95)
        if hasattr(result, "model_dump"):
            result = result.model_dump(mode="json")
        return result

    async def cleanup(self, context, status):
        if self.kind == "chapter_evaluate":
            async with context.session_factory() as session:
                chapter = await session.scalar(select(Chapter).where(
                    Chapter.project_id == context.project_id,
                    Chapter.chapter_number == context.payload["chapter_number"],
                ))
                if chapter and chapter.status == "evaluating":
                    chapter.status = (getattr(context, "previous_chapter_status", None) or "waiting_for_confirm") if status == "cancelled" else "evaluation_failed"
                    await session.commit()


def register_long_operations(runner):
    for kind in LONG_OPERATION_KINDS:
        runner.register(kind, LongOperationService(kind))
