import asyncio
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import Chapter, ChapterOutline, ChapterVersion, NovelBlueprint, NovelVolume
from app.schemas.novel import Blueprint
from app.services.llm_service import LLMService
from app.services.novel_service import NovelService
from test_concept_blueprint import converse


def core(total=40):
    return {'title': '自动分卷小说', 'genre': '悬疑', 'full_synopsis': '跨卷调查主线', 'target_chapter_range': {'min': total, 'max': total}}


def plan(total=40):
    count = max(1, total // 20)
    return {'total_chapters': total, 'volumes': [
        {'volume_number': index + 1, 'title': f'卷{index + 1}', 'summary': f'第{index + 1}卷的主线与核心冲突',
         'start_chapter': index * 20 + 1, 'end_chapter': min((index + 1) * 20, total),
         'main_plot': '调查', 'core_conflict': '阻力', 'climax': '揭晓', 'ending_hook': '下一卷线索', 'foreshadowing': '埋设并回收'}
        for index in range(count)]}


def batch(start, end):
    return {'chapters': [{'chapter_number': number, 'title': f'章{number}', 'summary': f'第{number}章推进调查并留下线索'} for number in range(start, end + 1)]}


async def submit_blueprint(client, auth, project, resume=True):
    response = await client.post(f'/api/novels/{project}/blueprint/generate', headers=auth, json={'resume': resume})
    assert response.status_code == 202
    assert list(response.json()) == ['job_id']
    return response.json()['job_id']


async def test_blueprint_job_stages_then_second_and_all_remaining_volumes(client, auth, project, llm, database, wait_job, job_runner):
    await converse(client, auth, project, llm)
    LLMService._resolve_llm_config.reset_mock()
    stages = []
    async def observe():
        current = (await job_runner.list(1, project_id=project, active=True))[0]
        stages.append(current.stage)
    llm.reply('screenwriting', core(60), before=observe)
    llm.reply('volume_planning', plan(60), before=observe)
    llm.reply('volume_outline', batch(1, 15), before=observe)
    llm.reply('volume_outline', batch(16, 20), before=observe)
    job = await wait_job(await submit_blueprint(client, auth, project))
    assert job['status'] == 'succeeded', job['error']
    assert stages == ['stage_core', 'stage_volume_plan', 'volume_1_batch_1_of_2', 'volume_1_batch_2_of_2']
    assert LLMService._resolve_llm_config.await_count == 1
    view = (await client.get(f'/api/novels/{project}', headers=auth)).json()
    assert view['blueprint']['total_chapters'] == 60
    assert len(view['blueprint']['volumes']) == 3
    assert [row['chapter_number'] for row in view['blueprint']['chapter_outline']] == list(range(1, 21))
    assert all(row['volume_number'] == 1 for row in view['blueprint']['chapter_outline'])
    assert [row['status'] for row in view['blueprint']['volumes']] == ['outlined', 'planned', 'planned']
    for start, end in [(21, 35), (36, 40)]:
        llm.reply('volume_outline', batch(start, end))
    response = await client.post(f'/api/novels/{project}/volumes/2/outline', headers=auth)
    assert response.status_code == 202
    assert (await wait_job(response.json()['job_id']))['status'] == 'succeeded'
    for start, end in [(41, 55), (56, 60)]:
        llm.reply('volume_outline', batch(start, end))
    response = await client.post(f'/api/novels/{project}/volumes/outline-all', headers=auth)
    assert response.status_code == 202
    assert (await wait_job(response.json()['job_id']))['status'] == 'succeeded'
    async with database() as session:
        outlines = (await session.scalars(select(ChapterOutline).order_by(ChapterOutline.chapter_number))).all()
        assert [row.chapter_number for row in outlines] == list(range(1, 61))
        assert [row.volume_number for row in outlines] == [1] * 20 + [2] * 20 + [3] * 20
        assert all(row.status == 'outlined' and row.outline_cursor == row.end_chapter for row in (await session.scalars(select(NovelVolume))).all())


async def test_failed_batch_resumes_cursor_without_repeating_core_plan_or_first_batch(client, auth, project, llm, database, wait_job):
    await converse(client, auth, project, llm)
    llm.reply('screenwriting', core(20))
    llm.reply('volume_planning', plan(20))
    llm.reply('volume_outline', batch(1, 15))
    async def fail():
        raise HTTPException(status_code=503, detail='模拟第二批失败')
    llm.reply('volume_outline', 'unused', before=fail)
    failed = await wait_job(await submit_blueprint(client, auth, project))
    assert failed['status'] == 'failed' and failed['error']['status_code'] == 503
    async with database() as session:
        rows = (await session.scalars(select(ChapterOutline).order_by(ChapterOutline.chapter_number))).all()
        original = [(row.id, row.chapter_number, row.title) for row in rows]
        assert len(original) == 15
        volume = (await session.scalars(select(NovelVolume))).one()
        assert volume.outline_cursor == 15 and volume.status == 'planned'
    llm.reply('volume_outline', batch(16, 20))
    resumed = await wait_job(await submit_blueprint(client, auth, project))
    assert resumed['status'] == 'succeeded', resumed['error']
    assert [name for name, _ in llm.calls].count('screenwriting') == 1
    assert [name for name, _ in llm.calls].count('volume_planning') == 1
    async with database() as session:
        rows = (await session.scalars(select(ChapterOutline).order_by(ChapterOutline.chapter_number))).all()
        assert [(row.id, row.chapter_number, row.title) for row in rows[:15]] == original
        assert [row.chapter_number for row in rows] == list(range(1, 21))


async def test_cancel_preserves_plan_and_new_job_can_resume(client, auth, project, llm, database, wait_job):
    await converse(client, auth, project, llm)
    started = asyncio.Event()
    stopped = asyncio.Event()
    async def block():
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            stopped.set()
    llm.reply('screenwriting', core(20))
    llm.reply('volume_planning', plan(20))
    llm.reply('volume_outline', 'unused', before=block)
    job_id = await submit_blueprint(client, auth, project)
    await asyncio.wait_for(started.wait(), 3)
    assert (await client.post(f'/api/jobs/{job_id}/cancel', headers=auth)).json()['status'] == 'cancelled'
    assert stopped.is_set()
    async with database() as session:
        volume = (await session.scalars(select(NovelVolume))).one()
        assert (volume.status, volume.outline_cursor) == ('planned', 0)
        assert (await session.scalars(select(ChapterOutline))).all() == []
    llm.reply('volume_outline', batch(1, 15))
    llm.reply('volume_outline', batch(16, 20))
    response = await client.post(f'/api/novels/{project}/volumes/1/outline', headers=auth)
    assert (await wait_job(response.json()['job_id']))['status'] == 'succeeded'


@pytest.mark.parametrize('stage', ['screenwriting', 'volume_planning', 'volume_outline'])
@pytest.mark.parametrize('finish', ['stop', 'length'])
async def test_any_truncated_stage_never_saves_partial_business_data(client, auth, project, llm, database, wait_job, stage, finish):
    await converse(client, auth, project, llm)
    if stage != 'screenwriting':
        llm.reply('screenwriting', core(20))
    if stage == 'volume_outline':
        llm.reply('volume_planning', plan(20))
    raw = {'screenwriting': '{"title":"残缺","target_chapter_range":',
           'volume_planning': '{"total_chapters":20,"volumes":[{"title":"半卷","summary":"截断"}',
           'volume_outline': '{"chapters":[{"chapter_number":1,"title":"半批","summary":"不能落库"},{"chapter_number":'}[stage]
    llm.reply(stage, raw, finish_reason=finish)
    job = await wait_job(await submit_blueprint(client, auth, project))
    assert job['status'] == 'failed' and job['error']['status_code'] == 500
    async with database() as session:
        assert (await session.scalars(select(ChapterOutline))).all() == []
        if stage != 'volume_outline':
            assert (await session.scalars(select(NovelVolume))).all() == []
            assert (await session.get(NovelBlueprint, project)).title is None


async def test_legacy_virtual_volume_smart_split_and_written_outline_protection(client, auth, ready_project, llm, database, wait_job):
    project = ready_project
    async with database() as session:
        for number in range(2, 41):
            session.add(ChapterOutline(project_id=project, chapter_number=number, title=f'旧章{number}', summary=f'旧摘要{number}', metadata={'keep': number}))
        chapter = Chapter(project_id=project, chapter_number=30, status='successful')
        session.add(chapter)
        await session.flush()
        session.add(ChapterVersion(chapter_id=chapter.id, version_label='v1', content='已经写好的正文'))
        await session.commit()
        original = [(row.id, row.chapter_number, row.title, row.summary, row.metadata) for row in (await session.scalars(select(ChapterOutline).order_by(ChapterOutline.chapter_number))).all()]
    legacy = (await client.get(f'/api/novels/{project}/volumes', headers=auth)).json()
    assert len(legacy) == 1 and legacy[0]['is_virtual'] and legacy[0]['end_chapter'] == 40
    llm.reply('volume_planning', plan(40))
    response = await client.post(f'/api/novels/{project}/volumes/smart-split', headers=auth)
    assert (await wait_job(response.json()['job_id']))['status'] == 'succeeded'
    async with database() as session:
        rows = (await session.scalars(select(ChapterOutline).order_by(ChapterOutline.chapter_number))).all()
        assert [(row.id, row.chapter_number, row.title, row.summary, row.metadata) for row in rows] == original
        assert [row.volume_number for row in rows] == [1] * 20 + [2] * 20
        saved = await session.scalar(select(ChapterOutline).where(ChapterOutline.chapter_number == 30))
        protected = (saved.id, saved.title, saved.summary, saved.metadata)
        await NovelService(session).replace_blueprint(project, Blueprint(title='手工新蓝图', chapter_outline=[]))
    view = (await client.get(f'/api/novels/{project}', headers=auth)).json()
    assert [row['chapter_number'] for row in view['blueprint']['chapter_outline']] == [30]
    async with database() as session:
        row = (await session.scalars(select(ChapterOutline))).one()
        assert (row.id, row.title, row.summary, row.metadata) == protected
        assert (await session.scalars(select(ChapterVersion))).one().content == '已经写好的正文'


async def test_patch_rejects_overlap_and_moving_written_chapter(client, auth, ready_project, llm, database, wait_job):
    project = ready_project
    async with database() as session:
        for number in range(2, 41):
            session.add(ChapterOutline(project_id=project, chapter_number=number, title=str(number), summary='摘要'))
        chapter = Chapter(project_id=project, chapter_number=18, status='successful')
        session.add(chapter)
        await session.flush()
        session.add(ChapterVersion(chapter_id=chapter.id, version_label='v1', content='保留正文'))
        await session.commit()
    llm.reply('volume_planning', plan())
    response = await client.post(f'/api/novels/{project}/volumes/smart-split', headers=auth)
    await wait_job(response.json()['job_id'])
    assert (await client.patch(f'/api/novels/{project}/volumes/1', headers=auth, json={'end_chapter': 25})).status_code == 422
    failed = await client.patch(f'/api/novels/{project}/volumes/1', headers=auth, json={'end_chapter': 17})
    assert failed.status_code == 409 and '已有正文' in failed.json()['detail']
    success = await client.patch(f'/api/novels/{project}/volumes/1', headers=auth, json={'title': '新卷名', 'summary': '新的确认梗概'})
    assert success.status_code == 200
    assert success.json()['blueprint']['volumes'][0]['title'] == '新卷名'


async def test_legacy_outline_request_only_sends_ten_recent_outlines(client, auth, ready_project, llm, database):
    async with database() as session:
        for number in range(2, 51):
            session.add(ChapterOutline(project_id=ready_project, chapter_number=number, title=f'旧章{number}',
                                       summary=f'OMITTED_EARLY_{number}' if number < 41 else f'RECENT_{number}'))
        await session.commit()
    llm.reply('outline_generation', batch(51, 52))
    response = await client.post(f'/api/writer/novels/{ready_project}/chapters/outline', headers=auth, json={'start_chapter': 51, 'num_chapters': 2})
    assert response.status_code == 200
    prompt = llm.calls[-1][1]['messages'][-1].content
    assert 'OMITTED_EARLY_' not in prompt and 'RECENT_41' in prompt and 'RECENT_50' in prompt
    assert prompt.count('RECENT_') == 10 and '[本卷计划]' in prompt


async def test_regeneration_preserves_written_outline_and_failed_core_can_restart(client, auth, ready_project, llm, database, wait_job):
    project = ready_project
    await converse(client, auth, project, llm)
    async with database() as session:
        outline = (await session.scalars(select(ChapterOutline))).one()
        outline.metadata = {'protected': True}
        original = (outline.id, outline.title, outline.summary, outline.metadata)
        chapter = Chapter(project_id=project, chapter_number=1, status='successful')
        session.add(chapter)
        await session.flush()
        session.add(ChapterVersion(chapter_id=chapter.id, version_label='v1', content='原有正文'))
        await session.commit()
    for retry in range(2):
        llm.reply('screenwriting', core(20))
        llm.reply('volume_planning', plan(20))
        llm.reply('volume_outline', batch(2, 16))
        llm.reply('volume_outline', batch(17, 20))
        succeeded = await wait_job(await submit_blueprint(client, auth, project, resume=retry > 0))
        assert succeeded['status'] == 'succeeded', succeeded['error']
        async with database() as session:
            kept = await session.scalar(select(ChapterOutline).where(ChapterOutline.chapter_number == 1))
            assert (kept.id, kept.title, kept.summary, kept.metadata) == original
            assert (await session.scalars(select(ChapterVersion))).one().content == '原有正文'
        if retry == 0:
            before = (await client.get(f'/api/novels/{project}', headers=auth)).json()
            llm.reply('screenwriting', '{"title":')
            failed = await wait_job(await submit_blueprint(client, auth, project, resume=False))
            assert failed['status'] == 'failed'
            assert (await client.get(f'/api/novels/{project}', headers=auth)).json() == before
            async with database() as session:
                assert (await session.get(NovelBlueprint, project)).generation_state is None
    assert [name for name, _ in llm.calls].count('screenwriting') == 3


async def test_active_planning_conflicts_with_manual_save_patch_and_chapter_jobs(client, auth, ready_project, llm, database, wait_job):
    project = ready_project
    async with database() as session:
        session.add(NovelVolume(project_id=project, volume_number=1, title='卷', summary='梗概', start_chapter=1, end_chapter=20))
        await session.commit()
    started = asyncio.Event()
    async def block():
        started.set()
        await asyncio.sleep(30)
    llm.reply('volume_outline', 'unused', before=block)
    response = await client.post(f'/api/novels/{project}/volumes/1/outline', headers=auth)
    job_id = response.json()['job_id']
    await asyncio.wait_for(started.wait(), 3)
    operations = [
        client.post(f'/api/novels/{project}/blueprint/save', headers=auth, json={'title': '不应覆盖'}),
        client.patch(f'/api/novels/{project}/blueprint', headers=auth, json={'chapter_outline': []}),
        client.patch(f'/api/novels/{project}/volumes/1', headers=auth, json={'title': '不应覆盖'}),
        client.post(f'/api/writer/novels/{project}/chapters/generate', headers=auth, json={'chapter_number': 1}),
    ]
    results = await asyncio.gather(*operations)
    assert all(result.status_code == 409 for result in results)
    assert all(result.json()['detail']['job_id'] == job_id for result in results)
    await client.post(f'/api/jobs/{job_id}/cancel', headers=auth)
    assert (await wait_job(job_id))['status'] == 'cancelled'
    async with database() as session:
        assert (await session.scalars(select(ChapterOutline))).one().title == '旧馆'


async def test_recovery_resets_outlining_flag_but_keeps_cursor(database, ready_project, job_runner):
    async with database() as session:
        session.add(NovelVolume(project_id=ready_project, volume_number=1, title='卷', summary='梗概', start_chapter=1, end_chapter=20, status='outlining', outline_cursor=15))
        await session.commit()
    await job_runner.recover()
    async with database() as session:
        volume = (await session.scalars(select(NovelVolume))).one()
        assert (volume.status, volume.outline_cursor) == ('planned', 15)


async def test_plan_checkpoint_and_public_title_commit_together_before_cancellation(database, ready_project, monkeypatch):
    from unittest.mock import AsyncMock
    from app.models import NovelProject
    from app.schemas.novel import Volume
    # 模拟第一笔业务事务已经提交、随后更新时间戳时被取消。
    monkeypatch.setattr(NovelService, '_touch_project', AsyncMock(side_effect=asyncio.CancelledError))
    checkpoint = {'stage': 'plan', 'core': {'title': '事务内的新标题'}}
    async with database() as session:
        with pytest.raises(asyncio.CancelledError):
            await NovelService(session).replace_blueprint(ready_project,
                Blueprint(title='事务内的新标题', total_chapters=20, volumes=[Volume(volume_number=1, title='第一卷', summary='梗概', start_chapter=1, end_chapter=20)]),
                checkpoint=checkpoint)
    async with database() as session:
        project = await session.get(NovelProject, ready_project)
        blueprint = await session.get(NovelBlueprint, ready_project)
        volume = (await session.scalars(select(NovelVolume))).one()
        assert (project.title, project.status) == ('事务内的新标题', 'blueprint_ready')
        assert blueprint.generation_state == checkpoint and blueprint.title == project.title
        assert (volume.start_chapter, volume.end_chapter) == (1, 20)


async def test_legacy_long_directory_remains_readable_beyond_new_generation_limit(client, auth, ready_project, database):
    async with database() as session:
        session.add(ChapterOutline(project_id=ready_project, chapter_number=10001, title='旧长篇尾章', summary='保留旧目录'))
        await session.commit()
    response = await client.get(f'/api/novels/{ready_project}', headers=auth)
    assert response.status_code == 200
    blueprint = response.json()['blueprint']
    assert blueprint['volumes'][0]['is_virtual'] and blueprint['volumes'][0]['end_chapter'] == 10001
    assert blueprint['total_chapters'] == 10001
    assert [row['chapter_number'] for row in blueprint['chapter_outline']] == [1, 10001]
