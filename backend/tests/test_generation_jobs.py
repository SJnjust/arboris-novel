import asyncio
from unittest.mock import AsyncMock

from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import settings
from app.models import Chapter, ChapterOutline, ChapterVersion, SystemConfig, UsageMetric
from app.services.llm_service import LLMService
from app.services.chapter_generation_service import ChapterGenerationService


async def test_parallel_versions_keep_successes_and_resolve_config_once(client, auth, ready_project, llm, database, wait_job, monkeypatch):
    async with database() as session:
        session.add(SystemConfig(key="writer.chapter_versions", value="3"))
        await session.commit()
    sessions = []
    original = LLMService._collect

    async def observe(self, messages, **kwargs):
        if messages[0]["content"] == "fixture:writing_v2":
            sessions.append(self.session)
            assert not self.session.in_transaction()
        return await original(self, messages, **kwargs)

    monkeypatch.setattr(LLMService, "_collect", observe)
    entered = active = peak = 0
    both = asyncio.Event()

    async def overlap():
        nonlocal entered, active, peak
        entered += 1
        active += 1
        peak = max(peak, active)
        if entered == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 1)
        await asyncio.sleep(0.01)
        active -= 1

    async def failing():
        await overlap()
        raise HTTPException(status_code=503, detail="模拟一个版本失败")

    llm.reply("chapter_plan", {"allowed_new_characters": []})
    llm.reply("writing_v2", {"content": "第一版正文。"}, before=overlap)
    llm.reply("writing_v2", "unused", before=failing)
    llm.reply("writing_v2", {"content": "第三版正文。"}, before=overlap)
    llm.reply("editor_review", {"best_version_index": 1, "scores": {"pacing": 9}})
    response = await client.post(f"/api/writer/novels/{ready_project}/chapters/generate", headers=auth, json={"chapter_number": 1})
    assert response.status_code == 202
    job = await wait_job(response.json()["job_id"])
    assert job["status"] == "succeeded", job["error"]
    assert job["result"]["version_count"] == 2
    assert job["result"]["failed_versions"] == [{"index": 1, "status_code": 503, "detail": "模拟一个版本失败"}]
    assert peak == 2
    assert len(sessions) == 3 and len({id(session) for session in sessions}) == 3
    assert LLMService._resolve_llm_config.await_count == 1
    async with database() as session:
        chapter = (await session.scalars(select(Chapter))).one()
        assert chapter.status == "waiting_for_confirm"
        versions = (await session.scalars(select(ChapterVersion).order_by(ChapterVersion.id))).all()
        assert [item.content for item in versions] == ["第一版正文。", "第三版正文。"]
        assert [item.metadata["requested_version_index"] for item in versions] == [0, 2]
        assert all(item.metadata["failed_versions"] == job["result"]["failed_versions"] for item in versions)
        assert (await session.get(UsageMetric, "api_request_count")).value == 4


async def test_cancel_generation_stops_all_versions_and_repairs_chapter(client, auth, ready_project, llm, database, wait_job):
    entered = 0
    started = asyncio.Event()
    ended = []

    async def blocked():
        nonlocal entered
        entered += 1
        if entered == 2:
            started.set()
        try:
            await asyncio.sleep(30)
        finally:
            ended.append(True)

    llm.reply("chapter_plan", {"allowed_new_characters": []})
    for _ in range(2):
        llm.reply("writing_v2", "late", before=blocked)
    response = await client.post(f"/api/writer/novels/{ready_project}/chapters/generate", headers=auth, json={"chapter_number": 1})
    assert response.status_code == 202
    await asyncio.wait_for(started.wait(), 2)
    job_id = response.json()["job_id"]
    assert (await client.post(f"/api/jobs/{job_id}/cancel", headers=auth)).json()["status"] == "cancelled"
    assert (await wait_job(job_id))["status"] == "cancelled"
    assert len(ended) == 2
    async with database() as session:
        assert (await session.scalars(select(Chapter))).one().status == "failed"
        assert (await session.scalars(select(ChapterVersion))).all() == []


async def test_backfill_is_recent_bounded_and_parallel_without_transactions(client, auth, ready_project, llm, database, wait_job, monkeypatch):
    monkeypatch.setattr(settings, "chapter_summary_backfill_limit", 3)
    async with database() as session:
        session.add(SystemConfig(key="writer.chapter_versions", value="1"))
        for number in range(1, 6):
            if number > 1:
                session.add(ChapterOutline(project_id=ready_project, chapter_number=number, title=f"章节{number}", summary="继续调查"))
            if number < 5:
                chapter = Chapter(project_id=ready_project, chapter_number=number, status="successful")
                session.add(chapter)
                await session.flush()
                version = ChapterVersion(chapter_id=chapter.id, content=f"已写正文{number}", version_label="v1")
                session.add(version)
                await session.flush()
                chapter.selected_version_id = version.id
        await session.commit()
    original = LLMService.get_summary
    seen_sessions = []

    async def observe(self, *args, **kwargs):
        assert not self.session.in_transaction()
        seen_sessions.append(self.session)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(LLMService, "get_summary", observe)
    entered = 0
    all_three = asyncio.Event()

    async def overlap():
        nonlocal entered
        entered += 1
        if entered == 3:
            all_three.set()
        await asyncio.wait_for(all_three.wait(), 1)

    for number in [2, 3, 4]:
        llm.reply("extraction", f"摘要{number}", before=overlap)
    llm.reply("chapter_plan", {"allowed_new_characters": []})
    llm.reply("writing_v2", "新的正文。")
    response = await client.post(f"/api/writer/novels/{ready_project}/chapters/generate", headers=auth, json={"chapter_number": 5})
    job = await wait_job(response.json()["job_id"])
    assert job["status"] == "succeeded", job["error"]
    assert entered == 3 and len({id(session) for session in seen_sessions}) == 3
    assert LLMService._resolve_llm_config.await_count == 1
    summary_inputs = [call[1]["messages"][-1].content for call in llm.calls if call[0] == "extraction"]
    assert summary_inputs == ["已写正文2", "已写正文3", "已写正文4"]
    async with database() as session:
        chapters = (await session.scalars(select(Chapter).order_by(Chapter.chapter_number))).all()
        assert chapters[0].real_summary is None
        assert [item.real_summary for item in chapters[1:4]] == ["摘要2", "摘要3", "摘要4"]
        assert chapters[4].status == "waiting_for_confirm"


async def test_select_uses_actual_ingestion_with_outline_title_and_user(client, auth, ready_project, database, monkeypatch, job_runner):
    from conftest import REAL_INGEST_CHAPTER
    from app.services import chapter_ingest_service
    from app.services.chapter_ingest_service import ChapterIngestionService
    from types import SimpleNamespace
    vector = SimpleNamespace(delete_by_chapters=AsyncMock(), upsert_chunks=AsyncMock(), upsert_summaries=AsyncMock())
    monkeypatch.setattr(settings, "vector_db_url", "file:fixture-only")
    monkeypatch.setattr(chapter_ingest_service, "VectorStoreService", lambda: vector)
    monkeypatch.setattr(ChapterIngestionService, "ingest_chapter", REAL_INGEST_CHAPTER)
    monkeypatch.setattr(LLMService, "get_summary", AsyncMock(return_value="选定正文的新摘要"))
    embeddings = AsyncMock(return_value=[0.1, 0.2])
    monkeypatch.setattr(LLMService, "get_embedding", embeddings)
    async with database() as session:
        chapter = Chapter(project_id=ready_project, chapter_number=1, status="waiting_for_confirm")
        session.add(chapter)
        await session.flush()
        session.add(ChapterVersion(chapter_id=chapter.id, version_label="v1", content="这里是真实经过入库方法处理的正文"))
        await session.commit()
    response = await client.post(f"/api/writer/novels/{ready_project}/chapters/select", headers=auth,
                                 json={"chapter_number": 1, "version_index": 0})
    assert response.status_code == 200
    await asyncio.gather(*job_runner.owned_tasks)
    vector.delete_by_chapters.assert_awaited_once_with(ready_project, [1])
    vector.upsert_chunks.assert_awaited_once()
    record = vector.upsert_chunks.await_args.kwargs["records"][0]
    assert (record["project_id"], record["chapter_number"], record["chapter_title"]) == (ready_project, 1, "旧馆")
    assert record["content"] == "这里是真实经过入库方法处理的正文"
    assert embeddings.await_args.kwargs["user_id"] == 1


async def test_large_version_count_keeps_scheduling_bounded_and_cancellable(monkeypatch):
    from types import SimpleNamespace
    service = ChapterGenerationService()
    both = asyncio.Event()
    started = []

    async def generate(*args):
        started.append(True)
        if len(started) == 2:
            both.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(service, "_version", generate)
    context = SimpleNamespace(check_cancelled=lambda: None, advance=AsyncMock())
    before = len(asyncio.all_tasks())
    task = asyncio.create_task(service._versions(context, {"count": 5000}, {}, lambda: None))
    try:
        await asyncio.wait_for(both.wait(), 1)
        assert len(started) == 2
        assert len(asyncio.all_tasks()) - before <= 5
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()


async def test_guardrail_violation_saves_the_rewritten_version(client, auth, ready_project, llm, database, wait_job):
    from app.models import Prompt
    async with database() as session:
        session.add(SystemConfig(key="writer.chapter_versions", value="1"))
        session.add(Prompt(name="rewrite_guardrails", content="fixture:rewrite_guardrails"))
        await session.commit()
    llm.reply("chapter_plan", {"allowed_new_characters": []})
    llm.reply("writing_v2", "与此同时，另一边发生了读者不应知道的事。")
    revised = "旧馆的窗外只有雨声，调查仍在眼前继续。"
    llm.reply("rewrite_guardrails", revised)
    response = await client.post(f"/api/writer/novels/{ready_project}/chapters/generate", headers=auth, json={"chapter_number": 1})
    assert response.status_code == 202
    job = await wait_job(response.json()["job_id"])
    assert job["status"] == "succeeded", job["error"]
    assert [name for name, _ in llm.calls] == ["chapter_plan", "writing_v2", "rewrite_guardrails"]
    rewrite_input = llm.calls[-1][1]["messages"][-1].content
    assert "与此同时" in rewrite_input and "全知视角" in rewrite_input
    async with database() as session:
        version = (await session.scalars(select(ChapterVersion))).one()
        assert version.content == revised
        assert version.metadata["guardrail"]["passed"] is False
        assert version.metadata["guardrail"]["rewritten"] is True
        assert version.metadata["guardrail"]["violations"]
        assert version.metadata["guardrail"]["violations"][0]["type"] == "omniscient_cue"
        assert version.metadata["chapter_mission"] == {"allowed_new_characters": []}
        assert (await session.scalars(select(Chapter))).one().status == "waiting_for_confirm"


async def test_edit_maintenance_completes_summary_and_ingestion_without_name_error(ready_project, llm, database, job_runner):
    from app.services.chapter_ingest_service import ChapterIngestionService
    content = "编辑后的正文保留了新线索。"
    async with database() as session:
        chapter = Chapter(project_id=ready_project, chapter_number=1, status="successful")
        session.add(chapter)
        await session.flush()
        version = ChapterVersion(chapter_id=chapter.id, version_label="v1", content=content)
        session.add(version)
        await session.flush()
        chapter.selected_version_id = version.id
        await session.commit()
    llm.reply("extraction", "<think>省略</think>编辑后的摘要")
    job_runner.maintain_chapter(ready_project, 1, 1)
    await asyncio.gather(*job_runner.owned_tasks)
    ChapterIngestionService.ingest_chapter.assert_awaited_once()
    arguments = ChapterIngestionService.ingest_chapter.await_args.kwargs
    assert callable(arguments.pop("is_current"))
    assert arguments == dict(project_id=ready_project, chapter_number=1, title="旧馆", content=content, summary="编辑后的摘要", user_id=1)
    async with database() as session:
        chapter = (await session.scalars(select(Chapter))).one()
        assert chapter.real_summary == "编辑后的摘要" and chapter.status == "successful"
