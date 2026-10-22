"""Deterministic interleavings: real routes/ORM/ingestion, controlled slow provider/vector I/O."""
import asyncio
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models import Chapter, ChapterVersion
from app.services.chapter_ingest_service import ChapterIngestionService
from app.services.chapter_maintenance_service import ChapterMaintenanceService
from app.services.job_service import JobRunner
from app.services.llm_service import LLMService

FIRST = '第一份正文：调查员进入旧馆'
LATEST = '最后一次编辑的正文：调查员找到了缺页目录'


async def seed(database, project):
    async with database() as session:
        chapter = Chapter(project_id=project, chapter_number=1, status='successful', real_summary='旧摘要')
        session.add(chapter)
        await session.flush()
        version = ChapterVersion(chapter_id=chapter.id, content=FIRST, version_label='v1')
        session.add(version)
        await session.flush()
        chapter.selected_version_id = version.id
        await session.commit()


@pytest.mark.parametrize('phase', ['summary', 'embedding', 'delete', 'chunks'])
@pytest.mark.parametrize('first_route', ['edit-fast', 'select', 'apply-optimization'])
async def test_interleaved_edits_keep_summary_and_vectors_latest_and_serialize_same_chapter(
    client, auth, ready_project, database, job_runner, monkeypatch, phase, first_route,
):
    from conftest import REAL_INGEST_CHAPTER
    await seed(database, ready_project)
    entered, release = asyncio.Event(), asyncio.Event()
    summary_inputs, writes = [], []
    chunks, summaries = {}, {}
    concurrency, maximum = defaultdict(int), defaultdict(int)
    refresh = ChapterMaintenanceService._refresh

    async def measured(self, project, number, state):
        key = (project, number)
        concurrency[key] += 1
        maximum[key] = max(maximum[key], concurrency[key])
        try:
            return await refresh(self, project, number, state)
        finally:
            concurrency[key] -= 1

    async def pause():
        entered.set()
        await release.wait()

    async def summary(self, content, **kwargs):
        summary_inputs.append(content)
        if phase == 'summary' and content == FIRST:
            await pause()
        return '摘要：' + content

    async def embedding(self, content, **kwargs):
        if phase == 'embedding' and content == FIRST:
            await pause()
        return [float(len(content)), 0.5]

    async def delete(project, numbers):
        if phase == 'delete' and not entered.is_set():
            await pause()
        chunks.clear(); summaries.clear()
        writes.append(('delete', None))

    async def upsert_chunks(*, records):
        if phase == 'chunks' and records[0]['content'] == FIRST:
            await pause()
        for record in records:
            chunks[record['id']] = record
            writes.append(('chunk', record['content']))

    async def upsert_summaries(*, records):
        for record in records:
            summaries[record['id']] = record
            writes.append(('summary', record['summary']))

    vector = SimpleNamespace(delete_by_chapters=delete, upsert_chunks=upsert_chunks, upsert_summaries=upsert_summaries)
    monkeypatch.setattr(settings, 'vector_db_url', 'file:offline-vector-fixture')
    monkeypatch.setattr('app.services.chapter_ingest_service.VectorStoreService', lambda: vector)
    monkeypatch.setattr(ChapterIngestionService, 'ingest_chapter', REAL_INGEST_CHAPTER)
    monkeypatch.setattr(LLMService, 'get_summary', summary)
    monkeypatch.setattr(LLMService, 'get_embedding', embedding)
    monkeypatch.setattr(ChapterMaintenanceService, '_refresh', measured)
    base = f'/api/writer/novels/{ready_project}/chapters/'
    body = {'chapter_number': 1, 'version_index': 0} if first_route == 'select' else {'chapter_number': 1, 'content': FIRST}
    if first_route == 'apply-optimization':
        response = await client.post('/api/optimizer/apply-optimization', headers=auth,
            params={'project_id': ready_project, 'chapter_number': 1, 'optimized_content': FIRST})
    else:
        response = await client.post(base + first_route, headers=auth, json=body)
    assert response.status_code == 200
    await asyncio.wait_for(entered.wait(), 2)
    # A second request must return immediately while the first provider/vector call is blocked.
    response = await asyncio.wait_for(client.post(base + 'edit-fast', headers=auth,
        json={'chapter_number': 1, 'content': LATEST}), 2)
    assert response.status_code == 200
    assert response.json()['real_summary'] is None
    assert len(job_runner.owned_tasks) == len(job_runner.chapter_maintenance.pending) == 1
    assert summary_inputs == [FIRST]
    release.set()
    await asyncio.wait_for(asyncio.gather(*job_runner.owned_tasks), 3)
    assert maximum[(ready_project, 1)] == 1
    assert summary_inputs == [FIRST, LATEST]
    assert not job_runner.chapter_maintenance.pending
    async with database() as session:
        chapter = (await session.scalars(select(Chapter).where(Chapter.project_id == ready_project))).one()
        version = await session.get(ChapterVersion, chapter.selected_version_id)
        assert version.content == LATEST
        assert chapter.real_summary == '摘要：' + LATEST
    assert [record['content'] for record in chunks.values()] == [LATEST]
    assert [record['summary'] for record in summaries.values()] == ['摘要：' + LATEST]
    assert ('summary', '摘要：' + FIRST) not in writes
    # Already in-flight upserts cannot be recalled; the same worker must finish with the newest pass.
    if phase != 'chunks':
        assert ('chunk', FIRST) not in writes


async def test_queued_requests_coalesce_and_read_latest_body_when_slot_opens(client, auth, ready_project, database, job_runner, monkeypatch):
    await seed(database, ready_project)
    await job_runner._owned_gate.acquire()
    await job_runner._owned_gate.acquire()
    summary = AsyncMock(side_effect=lambda content, **kwargs: '摘要：' + content)
    monkeypatch.setattr(LLMService, 'get_summary', summary)
    for content in [FIRST, '中间的一次编辑', LATEST]:
        response = await client.post(f'/api/writer/novels/{ready_project}/chapters/edit-fast', headers=auth,
            json={'chapter_number': 1, 'content': content})
        assert response.status_code == 200
    assert len(job_runner.owned_tasks) == len(job_runner.chapter_maintenance.pending) == 1
    summary.assert_not_awaited()
    job_runner._owned_gate.release()
    job_runner._owned_gate.release()
    await asyncio.gather(*job_runner.owned_tasks)
    assert summary.await_count == 1 and summary.await_args.args == (LATEST,)
    arguments = ChapterIngestionService.ingest_chapter.await_args.kwargs
    assert arguments['content'] == LATEST and arguments['summary'] == '摘要：' + LATEST


@pytest.mark.parametrize('change', ['content', 'version'])
async def test_summary_conditional_update_refuses_snapshot_changed_after_check(ready_project, database, job_runner, change):
    await seed(database, ready_project)
    service = job_runner.chapter_maintenance
    snapshot = await service._load(ready_project, 1)
    async with database() as session:
        chapter = (await session.scalars(select(Chapter))).one()
        if change == 'content':
            (await session.get(ChapterVersion, chapter.selected_version_id)).content = LATEST
        else:
            version = ChapterVersion(chapter_id=chapter.id, version_label='v2', content=FIRST)
            session.add(version)
            await session.flush()
            chapter.selected_version_id = version.id
        await session.commit()
    assert await service._save_summary(snapshot, '不应保存的过时摘要') is False
    async with database() as session:
        assert (await session.scalars(select(Chapter))).one().real_summary == '旧摘要'


async def test_maintenance_queue_is_bounded_and_cleans_cancelled_queued_keys():
    runner = JobRunner(None)
    await runner._owned_gate.acquire()
    await runner._owned_gate.acquire()
    for number in range(65):
        runner.maintain_chapter('project', number, 1)
    assert len(runner.owned_tasks) == len(runner.chapter_maintenance.pending) == 64
    runner.maintain_chapter('project', 0, 1)
    assert len(runner.owned_tasks) == 64
    await runner.shutdown()
    assert not runner.owned_tasks and not runner.chapter_maintenance.pending


async def test_latest_empty_body_clears_previous_vectors_without_summarizing(client, auth, ready_project, database, job_runner, monkeypatch):
    from conftest import REAL_INGEST_CHAPTER
    await seed(database, ready_project)
    vector = SimpleNamespace(delete_by_chapters=AsyncMock(), upsert_chunks=AsyncMock(), upsert_summaries=AsyncMock())
    monkeypatch.setattr(settings, 'vector_db_url', 'file:offline-vector-fixture')
    monkeypatch.setattr('app.services.chapter_ingest_service.VectorStoreService', lambda: vector)
    monkeypatch.setattr(ChapterIngestionService, 'ingest_chapter', REAL_INGEST_CHAPTER)
    summary = AsyncMock()
    monkeypatch.setattr(LLMService, 'get_summary', summary)
    response = await client.post(f'/api/writer/novels/{ready_project}/chapters/edit-fast', headers=auth,
        json={'chapter_number': 1, 'content': ''})
    assert response.status_code == 200
    await asyncio.gather(*job_runner.owned_tasks)
    summary.assert_not_awaited()
    vector.delete_by_chapters.assert_awaited_once_with(ready_project, [1])
    vector.upsert_chunks.assert_not_awaited()
    async with database() as session:
        assert (await session.scalars(select(Chapter))).one().real_summary is None
