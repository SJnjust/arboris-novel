"""Real HTTP → JobRunner → domain operations with only provider/embedding I/O stubbed."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models import Chapter, ChapterEvaluation, ChapterOutline, ChapterVersion, NovelBlueprint, Prompt
from app.services.llm_service import LLMService
from app.services.chapter_ingest_service import ChapterIngestionService

CASES = ['chapter_evaluate', 'outline_extend', 'chapter_optimize', 'novel_import',
         'emotion_analyze', 'six_dimension_review', 'consistency_review', 'concept_converse']


async def prepare(database, project):
    async with database() as session:
        chapter = Chapter(project_id=project, chapter_number=1, status='successful')
        session.add(chapter)
        await session.flush()
        version = ChapterVersion(chapter_id=chapter.id, content='林川走进旧馆，发现目录失踪了。', version_label='v1')
        session.add(version)
        await session.flush()
        chapter.selected_version_id = version.id
        session.add_all([Prompt(name='optimize_dialogue', content='optimize'), Prompt(name='six_dimension_review', content='six')])
        await session.commit()


def submission(kind, project):
    writer = f'/api/writer/novels/{project}/chapters/'
    return {
        'chapter_evaluate': (writer + 'evaluate', {'json': {'chapter_number': 1}}),
        'outline_extend': (writer + 'outline', {'json': {'start_chapter': 2, 'num_chapters': 1}}),
        'chapter_optimize': ('/api/optimizer/optimize', {'json': {'project_id': project, 'chapter_number': 1, 'dimension': 'dialogue'}}),
        'novel_import': ('/api/novels/import', {'files': {'file': ('novel.txt', '第一章 入口\n林川说道：“开始调查。”', 'text/plain')}}),
        'emotion_analyze': (f'/api/analytics/{project}/analyze-emotion-ai', {}),
        'six_dimension_review': ('/api/review/six-dimension', {'json': {'project_id': project, 'chapter_number': 1, 'chapter_content': '正文'}}),
        'consistency_review': ('/api/review/consistency', {'json': {'project_id': project, 'chapter_text': '正文'}}),
        'concept_converse': (f'/api/novels/{project}/concept/converse', {'json': {'user_input': {'value': '调查旧馆'}, 'conversation_state': {}}}),
    }[kind]


def reply(kind):
    return {
        'chapter_evaluate': '线索清楚，建议保留。',
        'outline_extend': {'chapters': [{'chapter_number': 2, 'title': '目录', 'summary': '发现新线索。'}]},
        'chapter_optimize': {'optimized_content': '优化后的正文。', 'optimization_notes': '精简对话'},
        'novel_import': {'title': '导入的旧馆', 'chapter_outline': [{'chapter_number': 1, 'title': '入口', 'summary': '进入旧馆'}]},
        'emotion_analyze': {'chapters': [{'chapter_number': 1, 'emotion_type': '惊讶', 'intensity': 7, 'description': '发现失踪目录'}]},
        'six_dimension_review': {'overall_score': 88, 'summary': '结构完整'},
        'consistency_review': {'is_consistent': False, 'violations': [{'severity': 'minor', 'category': 'plot', 'description': '需要补充线索'}], 'summary': '补充线索'},
        'concept_converse': {'ai_message': '从哪里开始调查？', 'ui_control': {'type': 'text_input'}, 'conversation_state': {'genre': '悬疑'}, 'is_complete': False},
    }[kind]


@pytest.mark.parametrize('kind', CASES)
async def test_long_operations_submit_before_model_completes_and_persist_results(client, auth, ready_project, database, monkeypatch, wait_job, kind):
    await prepare(database, ready_project)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def provider(self, **kwargs):
        calls.append(kwargs)
        entered.set()
        await release.wait()
        if kind == 'novel_import' and len(calls) == 1:
            return '["林川"]'
        data = reply(kind)
        return data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)

    monkeypatch.setattr(LLMService, 'get_llm_response', provider)
    if kind == 'concept_converse':
        async def stream(self, *args, **kwargs):
            yield await provider(self, **kwargs)
        monkeypatch.setattr(LLMService, 'stream_text', stream)
    url, body = submission(kind, ready_project)
    response = await asyncio.wait_for(client.post(url, headers={**auth, 'Idempotency-Key': f'operation-{kind}'}, **body), 2)
    assert response.status_code == 202
    await asyncio.wait_for(entered.wait(), 2)
    job_id = response.json()['job_id']
    running = (await client.get(f'/api/jobs/{job_id}', headers=auth)).json()
    assert running['kind'] == kind and running['status'] == 'running'
    assert 10 <= running['progress'] < 100
    replay = await client.post(url, headers={**auth, 'Idempotency-Key': f'operation-{kind}'}, **body)
    assert replay.json()['job_id'] == job_id and len(calls) == 1
    release.set()
    job = await wait_job(job_id)
    assert job['status'] == 'succeeded', job['error']
    assert job['progress'] == 100
    result = job['result']
    if kind == 'chapter_evaluate':
        assert result['chapters'][0]['evaluation'] == '线索清楚，建议保留。'
        async with database() as session:
            assert (await session.scalars(select(ChapterEvaluation))).one().decision == 'reviewed'
    elif kind == 'outline_extend':
        assert [row['chapter_number'] for row in result['blueprint']['chapter_outline']] == [1, 2]
    elif kind == 'chapter_optimize':
        assert result == {**reply(kind), 'dimension': 'dialogue'}
        async with database() as session:
            assert (await session.scalars(select(ChapterVersion))).one().content.startswith('林川')
    elif kind == 'novel_import':
        assert job['payload'] == {'filename': 'novel.txt'}
        async with database() as session:
            assert (await session.get(NovelBlueprint, result['id'])).title == '导入的旧馆'
            chapters = (await session.scalars(select(Chapter).where(Chapter.project_id == result['id']))).all()
            assert len(chapters) == 1 and chapters[0].selected_version_id
    elif kind == 'emotion_analyze':
        assert result['emotion_points'][0]['intensity'] == 7 and result['average_intensity'] == 7
    elif kind in ('six_dimension_review', 'consistency_review'):
        assert result['review']['summary'] == reply(kind)['summary']
    else:
        assert result['ai_message'] == reply(kind)['ai_message']


@pytest.mark.parametrize('kind', CASES)
@pytest.mark.parametrize('cancel', [False, True])
async def test_long_operations_can_fail_safely_or_cancel_upstream(client, auth, ready_project, database, monkeypatch, wait_job, kind, cancel):
    await prepare(database, ready_project)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def provider(self, **kwargs):
        entered.set()
        if not cancel:
            raise RuntimeError('provider private-internal-diagnostic')
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(LLMService, 'get_llm_response', provider)
    if kind == 'concept_converse':
        async def stream(self, *args, **kwargs):
            yield await provider(self, **kwargs)
        monkeypatch.setattr(LLMService, 'stream_text', stream)
    url, body = submission(kind, ready_project)
    response = await client.post(url, headers=auth, **body)
    assert response.status_code == 202
    await asyncio.wait_for(entered.wait(), 2)
    job_id = response.json()['job_id']
    if cancel:
        assert (await client.post(f'/api/jobs/{job_id}/cancel', headers=auth)).status_code == 200
        await asyncio.wait_for(cancelled.wait(), 2)
    job = await wait_job(job_id)
    assert job['status'] == ('cancelled' if cancel else 'failed')
    assert 'private-internal-diagnostic' not in json.dumps(job, ensure_ascii=False)
    if kind == 'novel_import':
        assert 'content' not in job['payload']
    if kind == 'chapter_evaluate':
        async with database() as session:
            chapter = (await session.scalars(select(Chapter).where(Chapter.project_id == ready_project))).one()
            assert chapter.status == ('successful' if cancel else 'evaluation_failed')


async def test_select_returns_while_embedding_is_still_running(client, auth, ready_project, database, monkeypatch, job_runner):
    await prepare(database, ready_project)
    entered, release = asyncio.Event(), asyncio.Event()

    async def ingest(self, **kwargs):
        entered.set()
        await release.wait()

    monkeypatch.setattr(LLMService, 'get_summary', AsyncMock(return_value='选版摘要'))
    monkeypatch.setattr(ChapterIngestionService, 'ingest_chapter', ingest)
    response = await asyncio.wait_for(client.post(f'/api/writer/novels/{ready_project}/chapters/select', headers=auth,
        json={'chapter_number': 1, 'version_index': 0}), 2)
    assert response.status_code == 200
    assert response.json()['chapters'][0]['generation_status'] == 'successful'
    await asyncio.wait_for(entered.wait(), 2)
    assert job_runner.owned_tasks
    release.set()
    await asyncio.gather(*job_runner.owned_tasks)


async def test_import_rejects_large_or_empty_uploads_before_scheduling(client, auth):
    response = await client.post('/api/novels/import', headers=auth, files={'file': ('novel.txt', b'a' * (8 * 1024 * 1024 + 1))})
    assert response.status_code == 413
    response = await client.post('/api/novels/import', headers=auth, files={'file': ('novel.txt', b'  ')})
    assert response.status_code == 400
    assert (await client.get('/api/jobs?active=1', headers=auth)).json() == []


async def test_provider_internal_errors_never_return_provider_payload(database, monkeypatch):
    import httpx
    from openai import InternalServerError
    from app.services import llm_service

    class Client:
        def __init__(self, **kwargs): pass
        async def stream_chat(self, **kwargs):
            response = httpx.Response(500, request=httpx.Request('POST', 'http://offline.invalid'))
            raise InternalServerError('private diagnostic', response=response, body={'error': {'message': 'private provider content'}})
            yield

    monkeypatch.setattr(llm_service, 'LLMClient', Client)
    async with database() as session:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            await LLMService(session).get_llm_response('offline', [])
    assert error.value.status_code == 503
    assert error.value.detail == 'AI 服务内部错误，请稍后重试'


@pytest.mark.parametrize('route', ['edit', 'edit-fast'])
async def test_first_manual_edit_loads_relationships_and_persists_content(client, auth, ready_project, database, monkeypatch, job_runner, route):
    monkeypatch.setattr(LLMService, 'get_summary', AsyncMock(return_value='已保存的摘要'))
    response = await client.post(f'/api/writer/novels/{ready_project}/chapters/{route}', headers=auth,
        json={'chapter_number': 1, 'content': '第一次手工写下的完整正文。'})
    assert response.status_code == 200
    body = response.json()['chapters'][0] if route == 'edit' else response.json()
    assert body['generation_status'] == 'successful' and body['content'] == '第一次手工写下的完整正文。'
    await asyncio.gather(*job_runner.owned_tasks)
    async with database() as session:
        chapter = (await session.scalars(select(Chapter).where(Chapter.project_id == ready_project))).one()
        version = await session.get(ChapterVersion, chapter.selected_version_id)
        assert version.content == body['content']
        assert chapter.real_summary == '已保存的摘要'
