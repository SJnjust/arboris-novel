import asyncio
import json

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models import ChapterOutline, NovelBlueprint, NovelVolume
from app.services.volume_planning import normalize_volume_plan, normalize_outline_batch
from test_concept_blueprint import converse
from test_volume_jobs import batch


@pytest.mark.parametrize('failed_batch,new_size', [(1, 5), (2, 5), (2, 7), (2, 20)])
async def test_outline_timeout_keeps_plan_and_resumes_exact_chapter_cursor(client, auth, project, blueprint, llm, database, wait_job, monkeypatch, failed_batch, new_size):
    monkeypatch.setattr(settings, 'outline_batch_size', 5)
    monkeypatch.setattr(settings, 'llm_job_deadline_seconds', 0.08)
    monkeypatch.setattr(settings, 'llm_deadline_seconds', 0.01)
    monkeypatch.setattr(settings, 'llm_job_read_timeout_seconds', 1234)
    await converse(client, auth, project, llm)
    llm.reply('screenwriting', {**blueprint, 'target_chapter_range': {'min': 40, 'max': 40}})
    llm.reply('volume_planning', {'total_chapters': 40, 'volumes': [
        {'volume_number': number, 'title': f'卷{number}', 'summary': '完整梗概', 'start_chapter': 1 if number==1 else 21, 'end_chapter': 20 if number==1 else 40}
        for number in (1, 2)]})
    if failed_batch == 2:
        llm.reply('volume_outline', batch(1, 5))
    llm.reply('volume_outline', 'not delivered', before=lambda: asyncio.sleep(1))
    submitted = await client.post(f'/api/novels/{project}/blueprint/generate', headers=auth)
    assert submitted.status_code == 202
    failed = await wait_job(submitted.json()['job_id'])
    assert failed['status'] == 'failed' and failed['error']['status_code'] == 504
    assert failed['error']['stage'] == f'volume_1_batch_{failed_batch}_of_4'
    assert f'第 1 卷第 {failed_batch}/4 批大纲' in failed['error']['step']
    assert '总时限' in failed['error']['detail']
    async with database() as session:
        record = await session.get(NovelBlueprint, project)
        assert record.title == blueprint['title'] and record.generation_state['stage'] == 'plan'
        assert len((await session.scalars(select(NovelVolume))).all()) == 2
        assert [row.chapter_number for row in (await session.scalars(select(ChapterOutline).order_by(ChapterOutline.chapter_number))).all()] == list(range(1, 6 if failed_batch == 2 else 1))
    overview = (await client.get(f'/api/novels/{project}/sections/overview', headers=auth)).json()['data']
    assert any(row['volume_number'] == 2 for row in overview['missing_outline_volumes'])
    if failed_batch == 1:
        assert any(row['volume_number'] == 1 for row in overview['missing_outline_volumes'])
    monkeypatch.setattr(settings, 'outline_batch_size', new_size)
    monkeypatch.setattr(settings, 'llm_job_deadline_seconds', 2)
    cursor = 5 if failed_batch == 2 else 0
    for start in range(cursor + 1, 21, new_size):
        llm.reply('volume_outline', batch(start, min(20, start + new_size - 1)))
    submitted = await client.post(f'/api/novels/{project}/blueprint/generate', headers=auth, json={'resume': True})
    done = await wait_job(submitted.json()['job_id'])
    assert done['status'] == 'succeeded', done['error']
    view = (await client.get(f'/api/novels/{project}', headers=auth)).json()
    assert [row['chapter_number'] for row in view['blueprint']['chapter_outline']] == list(range(1, 21))
    assert view['blueprint']['volumes'][0]['status'] == 'outlined'
    names = [name for name, _ in llm.calls]
    assert names.count('screenwriting') == names.count('volume_planning') == 1
    assert all(call['read_timeout'] == 1234 for name, call in llm.calls)
    starts = [json.loads(call['messages'][-1].content.split('[本批任务]\n')[1].splitlines()[-1])['start_chapter']
              for name, call in llm.calls if name == 'volume_outline']
    assert starts[failed_batch:] == list(range(cursor + 1, 21, new_size))


async def test_long_volume_plan_field_budget_rejects_whole_plan_then_resumes_without_core(client, auth, project, blueprint, llm, database, wait_job, monkeypatch):
    monkeypatch.setattr(settings, 'outline_batch_size', 20)
    await converse(client, auth, project, llm)
    llm.reply('screenwriting', {**blueprint, 'target_chapter_range': {'min': 140, 'max': 140}})
    volumes = [{'volume_number': number, 'title': f'卷{number}', 'summary': '简洁的因果梗概',
                'start_chapter': 20*(number-1)+1, 'end_chapter': 20*number, 'main_plot': '主线', 'core_conflict': '冲突',
                'climax': '高潮', 'ending_hook': '钩子', 'foreshadowing': '回收伏笔'} for number in range(1, 8)]
    llm.reply('volume_planning', {'total_chapters': 140, 'volumes': [{**volumes[0], 'main_plot': '超'*81}, *volumes[1:]]})
    response = await client.post(f'/api/novels/{project}/blueprint/generate', headers=auth)
    failed = await wait_job(response.json()['job_id'])
    assert failed['error']['step'] == '分卷规划' and '超过 80 字' in failed['error']['detail']
    async with database() as session:
        assert (await session.get(NovelBlueprint, project)).generation_state['stage'] == 'core'
        assert (await session.scalars(select(NovelVolume))).all() == []
        assert (await session.scalars(select(ChapterOutline))).all() == []
    llm.reply('volume_planning', {'total_chapters': 140, 'volumes': volumes})
    llm.reply('volume_outline', batch(1, 20))
    response = await client.post(f'/api/novels/{project}/blueprint/generate', headers=auth)
    assert (await wait_job(response.json()['job_id']))['status'] == 'succeeded'
    assert [name for name, _ in llm.calls].count('screenwriting') == 1
    assert all('超过6卷' in call['messages'][0].content for name, call in llm.calls if name == 'volume_planning')


def test_structured_field_limits_and_twenty_chapter_batch():
    assert len(normalize_outline_batch(batch(1, 20), 1, 20)) == 20
    with pytest.raises(ValueError, match='过长'):
        normalize_outline_batch({'chapters': [{'chapter_number': 1, 'title': '章', 'summary': '字'*301}]}, 1, 1)
    with pytest.raises(ValueError, match='超过'):
        normalize_volume_plan({'volumes': [{'title': '卷', 'summary': '字'*241}]}, 20)
