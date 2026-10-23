import asyncio
from unittest.mock import AsyncMock

import pytest

from fastapi import HTTPException
from sqlalchemy import select

from app.models import AdminSetting, ChapterOutline, SystemConfig, UsageMetric, UserDailyRequest
from app.services.llm_service import LLMService
from app.services.usage_service import UsageService


async def test_daily_reservation_is_atomic_at_limit(database):
    async with database() as session:
        session.add(AdminSetting(key="daily_request_limit", value="1"))
        await session.commit()

    async def reserve():
        async with database() as session:
            try:
                return await LLMService(session)._enforce_daily_limit(1)
            except HTTPException as error:
                return error.status_code

    results = await asyncio.gather(reserve(), reserve())
    assert results.count(429) == 1
    assert len([value for value in results if isinstance(value, str)]) == 1
    async with database() as session:
        assert (await session.scalars(select(UserDailyRequest))).one().request_count == 1


async def test_parallel_jobs_aggregate_real_default_key_quota(client, auth, ready_project, llm, database, wait_job, monkeypatch):
    from conftest import REAL_RESOLVE_CONFIG
    resolved = 0

    async def resolve(self, user_id):
        nonlocal resolved
        resolved += 1
        return await REAL_RESOLVE_CONFIG(self, user_id)

    monkeypatch.setattr(LLMService, "_resolve_llm_config", resolve)
    async with database() as session:
        assert await UsageService(session).get_value("api_request_count") == 0
        assert await session.get(UsageMetric, "api_request_count") is None
        session.add(SystemConfig(key="llm.api_key", value="offline-quota-fixture"))
        session.add(ChapterOutline(project_id=ready_project, chapter_number=2, title="继续调查", summary="更多线索"))
        await session.commit()
    for _ in range(2):
        llm.reply("chapter_plan", {"allowed_new_characters": []})
        llm.reply("editor_review", {"best_version_index": 0, "scores": {"pacing": 8}})
    for number in range(4):
        llm.reply("writing_v2", f"配额验证正文{number}。")
    responses = await asyncio.gather(*(client.post(
        f"/api/writer/novels/{ready_project}/chapters/generate", headers=auth, json={"chapter_number": number}
    ) for number in [1, 2]))
    assert all(response.status_code == 202 for response in responses)
    jobs = await asyncio.gather(*(wait_job(response.json()["job_id"]) for response in responses))
    assert all(job["status"] == "succeeded" for job in jobs), [job["error"] for job in jobs]
    assert resolved == 2
    async with database() as session:
        assert (await session.get(UsageMetric, "api_request_count")).value == 8
        assert (await session.scalars(select(UserDailyRequest))).one().request_count == 8


async def test_fixed_embedding_config_needs_no_session_and_records_completion(llm, monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from app.services import llm_service
    response = SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2])])
    create = AsyncMock(return_value=response)

    @asynccontextmanager
    async def sdk(*args, **kwargs):
        yield SimpleNamespace(embeddings=SimpleNamespace(create=create))

    monkeypatch.setattr(llm_service, "openai_client", sdk)
    calls = []
    config = {"api_key": "offline", "_settings": {"embedding.model": "fixture-embedding"}}
    service = LLMService(None, config=config, record_usage=False, usage_callback=lambda kind: calls.append(kind))
    assert await service.get_embedding("待检索内容", user_id=1) == [0.1, 0.2]
    create.assert_awaited_once_with(input="待检索内容", model="fixture-embedding")
    assert calls == ["embedding"]
    assert LLMService._resolve_llm_config.await_count == 0


@pytest.mark.parametrize('outcome', ['failed', 'cancelled', 'setup_failed', 'one_completed'])
async def test_zero_completed_call_refunds_reservation(client, auth, ready_project, llm, database, wait_job, monkeypatch, outcome):
    from conftest import REAL_RESOLVE_CONFIG
    from app.models import Prompt
    from sqlalchemy import delete
    monkeypatch.setattr(LLMService, '_resolve_llm_config', REAL_RESOLVE_CONFIG)
    async with database() as session:
        session.add(SystemConfig(key='llm.api_key', value='offline-refund-fixture'))
        session.add(SystemConfig(key='writer.chapter_versions', value='1'))
        if outcome == 'setup_failed':
            await session.execute(delete(Prompt).where(Prompt.name.in_(['chapter_plan', 'writing_v2'])))
        await session.commit()
    started = asyncio.Event()

    async def fail():
        raise HTTPException(status_code=503, detail='模型尚未完成就失败')

    async def block():
        started.set()
        await asyncio.sleep(30)

    if outcome == 'cancelled':
        llm.reply('chapter_plan', 'unused', before=block)
    elif outcome != 'setup_failed':
        llm.reply('chapter_plan', {'allowed_new_characters': []}, before=None if outcome == 'one_completed' else fail)
        llm.reply('writing_v2', 'unused', before=fail)
    response = await client.post(f'/api/writer/novels/{ready_project}/chapters/generate', headers=auth, json={'chapter_number': 1})
    assert response.status_code == 202
    job_id = response.json()['job_id']
    if outcome == 'cancelled':
        await asyncio.wait_for(started.wait(), 2)
        assert (await client.post(f'/api/jobs/{job_id}/cancel', headers=auth)).status_code == 200
    job = await wait_job(job_id)
    assert job['status'] == ('cancelled' if outcome == 'cancelled' else 'failed')
    async with database() as session:
        expected = int(outcome == 'one_completed')
        assert (await session.scalars(select(UserDailyRequest))).one().request_count == expected
        assert await UsageService(session).get_value('api_request_count') == expected
