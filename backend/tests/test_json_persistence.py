import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models import Chapter, ChapterOutline, ChapterVersion, NovelProject, Prompt, SystemConfig
from app.models.memory_layer import CharacterState, TimelineEvent
from app.services.import_service import ImportService
from app.services.memory_layer_service import MemoryLayerService
from test_concept_blueprint import converse


@pytest.mark.parametrize('stage,partial', [
    ('concept', '{"ai_message":"不得保存","conversation_state":{"genre":"悬疑"},"ui_control":'),
    ('screenwriting', '{"title":"不得覆盖","chapter_outline":[{"chapter_number":2,"title":"半份","summary":"片段"},{"chapter_number":'),
    ('outline_generation', '{"chapters":[{"chapter_number":2,"title":"不得追加","summary":"片段"},{"chapter_number":'),
])
async def test_truncated_json_does_not_replace_or_append_business_data(client, auth, ready_project, llm, database, stage, partial):
    if stage == 'screenwriting':
        assert (await converse(client, auth, ready_project, llm)).status_code == 200
    before = (await client.get(f'/api/novels/{ready_project}', headers=auth)).json()
    llm.reply(stage, partial, finish_reason='stop')
    if stage == 'outline_generation':
        url = f'/api/writer/novels/{ready_project}/chapters/outline'
        body = {'start_chapter': 2, 'num_chapters': 2}
    else:
        url = f'/api/novels/{ready_project}/' + ('concept/converse' if stage == 'concept' else 'blueprint/generate')
        body = {'user_input': {'value': '测试'}, 'conversation_state': {}} if stage == 'concept' else None
    response = await client.post(url, headers=auth, **({'json': body} if body else {}))
    assert response.status_code == 500
    after = (await client.get(f'/api/novels/{ready_project}', headers=auth)).json()
    assert after == before
    async with database() as session:
        outlines = (await session.scalars(select(ChapterOutline))).all()
        assert [(row.chapter_number, row.title) for row in outlines] == [(1, '旧馆')]
        assert (await session.scalars(select(ChapterVersion))).all() == []


@pytest.mark.parametrize('partial', ['{"content":"半章正文', '```json\n{"content":"半章正文', '结果如下：{"content":"半章正文'])
async def test_truncated_json_version_is_not_saved_as_plain_text(client, auth, ready_project, llm, database, wait_job, partial):
    async with database() as session:
        session.add(SystemConfig(key='writer.chapter_versions', value='1'))
        await session.commit()
    llm.reply('chapter_plan', {'allowed_new_characters': []})
    llm.reply('writing_v2', partial, finish_reason='stop')
    response = await client.post(f'/api/writer/novels/{ready_project}/chapters/generate', headers=auth, json={'chapter_number': 1})
    assert response.status_code == 202
    job = await wait_job(response.json()['job_id'])
    assert job['status'] == 'failed'
    assert job['error']['status_code'] == 500
    assert '输出不完整' in job['error']['detail']['failed_versions'][0]['detail']
    async with database() as session:
        assert (await session.scalars(select(ChapterVersion))).all() == []
        chapter = (await session.scalars(select(Chapter))).one()
        assert chapter.status == 'failed' and chapter.selected_version_id is None


@pytest.mark.parametrize('stage', ['census', 'blueprint'])
async def test_truncated_import_does_not_create_project_or_versions(client, auth, database, monkeypatch, stage):
    census = '["有效名字", "未完成' if stage == 'census' else '["有效名字"]'
    replies = [census] if stage == 'census' else [census, '{"title":"不能落库","chapter_outline":[{"chapter_number":1,"title":"片段","summary":"摘要"}']
    monkeypatch.setattr('app.services.llm_service.LLMService.get_llm_response', AsyncMock(side_effect=replies))
    response = await client.post('/api/novels/import', headers=auth,
                                 files={'file': ('novel.txt', '第一章 起点\n有人走进了旧馆。'.encode(), 'text/plain')})
    assert response.status_code == 500
    assert '输出不完整' in response.json()['detail']
    async with database() as session:
        assert (await session.scalars(select(NovelProject))).all() == []
        assert (await session.scalars(select(ChapterVersion))).all() == []


async def test_truncated_memory_extraction_does_not_persist_partial_records(database, ready_project):
    llm = type('Model', (), {'get_llm_response': AsyncMock(side_effect=[
        '{"character_states":[{"character_name":"不得写入","location":"旧馆"},{"character_name":',
        '{"events":[{"event_title":"不得写入","event_description":"片段"},{"event_title":',
    ])})()
    async with database() as session:
        service = MemoryLayerService(session, llm, None)
        result = await service.update_memory_after_chapter(ready_project, 1, '正文', ['不得写入'], 1)
        assert result == {'character_states_updated': 0, 'timeline_events_added': 0, 'causal_chains_added': 0}
    async with database() as session:
        assert (await session.scalars(select(CharacterState))).all() == []
        assert (await session.scalars(select(TimelineEvent))).all() == []


@pytest.mark.parametrize('batch', [False, True])
async def test_truncated_blueprint_metadata_does_not_write_partial_records(database, ready_project, batch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.models.chapter_blueprint import ChapterBlueprint
    from app.services.blueprint_service import BlueprintService
    async with database() as session:
        url = session.bind.url.set(drivername='sqlite')
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            response = '[{"chapter_number":1,"chapter_focus":"不得保存"},{"chapter_number":' if batch else '{"chapter_focus":"不得保存","brief_summary":'
            model = type('Model', (), {'generate': AsyncMock(return_value=response)})()
            service = BlueprintService(session, model)
            result = await (service.generate_all_blueprints(ready_project, 1) if batch else service.generate_blueprint_from_outline(ready_project, 1, 1))
            assert result == ([] if batch else None)
            assert session.scalars(select(ChapterBlueprint)).all() == []
    finally:
        engine.dispose()
    async with database() as session:
        assert (await session.scalars(select(ChapterBlueprint))).all() == []


async def test_truncated_mission_and_review_do_not_become_persisted_metadata(client, auth, ready_project, llm, database, wait_job):
    llm.reply('chapter_plan', '{"macro_beat":"不应保存","allowed_new_characters":')
    llm.reply('writing_v2', '完整的第一版正文。')
    llm.reply('writing_v2', '完整的第二版正文。')
    llm.reply('editor_review', '{"best_version_index":0,"scores":{"pacing":9},"critical_flaws":')
    response = await client.post(f'/api/writer/novels/{ready_project}/chapters/generate', headers=auth, json={'chapter_number': 1})
    job = await wait_job(response.json()['job_id'])
    assert job['status'] == 'succeeded', job['error']
    async with database() as session:
        versions = (await session.scalars(select(ChapterVersion))).all()
        assert len(versions) == 2
        assert all(version.metadata['chapter_mission'] is None and 'ai_review' not in version.metadata for version in versions)


async def test_truncated_optimization_is_rejected_and_preserves_original_version(client, auth, ready_project, database, monkeypatch):
    async with database() as session:
        session.add(Prompt(name='optimize_dialogue', content='fixture:optimize_dialogue'))
        chapter = Chapter(project_id=ready_project, chapter_number=1, status='successful')
        session.add(chapter)
        await session.flush()
        version = ChapterVersion(chapter_id=chapter.id, version_label='v1', content='原有完整正文。')
        session.add(version)
        await session.flush()
        chapter.selected_version_id = version.id
        await session.commit()
    monkeypatch.setattr('app.services.llm_service.LLMService.get_llm_response', AsyncMock(return_value='{"optimized_content":"截断正文'))
    response = await client.post('/api/optimizer/optimize', headers=auth,
                                 json={'project_id': ready_project, 'chapter_number': 1, 'dimension': 'dialogue'})
    assert response.status_code == 500
    assert '输出不完整' in response.json()['detail']
    async with database() as session:
        versions = (await session.scalars(select(ChapterVersion))).all()
        assert len(versions) == 1 and versions[0].content == '原有完整正文。'
