from pathlib import Path

from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.init_db import _ensure_default_prompts, _ensure_schema_updates, _prompt_file
from app.models import Prompt
from app.schemas.prompt import PromptCreate, PromptUpdate
from app.services.prompt_service import PromptService


async def test_version_upgrade_preserves_custom_and_inserts_missing(database, tmp_path):
    folder = tmp_path / 'prompts'
    folder.mkdir()
    for name in ['stock', 'custom', 'missing']:
        (folder / f'{name}.md').write_text('<!-- version: 2 -->\n新发行正文')
    async with database() as session:
        session.add_all([Prompt(name='stock', content='旧发行正文', version=1),
                         Prompt(name='custom', content='管理员的内容', version=1, is_customized=True)])
        await session.commit()
        await _ensure_default_prompts(session, folder)
        await session.commit()
        records = {row.name: row for row in (await session.scalars(select(Prompt))).all()}
        assert (records['stock'].content, records['stock'].version, records['stock'].is_customized) == ('新发行正文', 2, False)
        assert (records['custom'].content, records['custom'].version, records['custom'].is_customized) == ('管理员的内容', 1, True)
        assert (records['missing'].content, records['missing'].version) == ('新发行正文', 2)
        (folder / 'stock.md').write_text('<!-- version: 1 -->\n不应降级')
        await _ensure_default_prompts(session, folder)
        await session.commit()
        assert records['stock'].content == '新发行正文'


async def test_first_upgrade_marks_different_content_and_updates_shipped_concept(database, tmp_path):
    folder = tmp_path / 'prompts'
    folder.mkdir()
    (folder / 'baselines').mkdir()
    (folder / 'concept.md').write_text('<!-- version: 2 -->\n包含已收集信息的新正文')
    (folder / 'baselines/concept.v1.md').write_text('<!-- version: 1 -->\n旧版原始正文')
    (folder / 'same.md').write_text('<!-- version: 1 -->\n未改过的正文')
    (folder / 'edited.md').write_text('<!-- version: 2 -->\n发行版正文')
    async with database() as session:
        # version=0 是历史 ALTER 的哨兵，仅首次升级进行内容比对。
        await session.execute(text("UPDATE prompts SET version=0"))
        session.add_all([Prompt(name='concept-file', content='unused')])
        existing = await session.scalar(select(Prompt).where(Prompt.name == 'concept'))
        existing.content = '旧版原始正文'
        session.add_all([Prompt(name='same', content='未改过的正文', version=0),
                         Prompt(name='edited', content='真正的管理员定制', version=0)])
        await session.commit()
        await _ensure_default_prompts(session, folder)
        await session.commit()
        assert (existing.content, existing.version, existing.is_customized) == ('包含已收集信息的新正文', 2, False)
        same = await session.scalar(select(Prompt).where(Prompt.name == 'same'))
        edited = await session.scalar(select(Prompt).where(Prompt.name == 'edited'))
        assert same.content == '未改过的正文' and not same.is_customized and same.version == 1
        assert edited.content == '真正的管理员定制' and edited.is_customized and edited.version == 1
        await _ensure_default_prompts(session, folder)
        await session.commit()
        assert edited.content == '真正的管理员定制'


async def test_admin_edits_mark_custom_and_invalidate_cache(database):
    async with database() as session:
        service = PromptService(session)
        before = await service.get_prompt('concept')
        prompt = await session.scalar(select(Prompt).where(Prompt.name == 'concept'))
        result = await service.update_prompt(prompt.id, PromptUpdate(content='管理员新正文'))
        assert result.is_customized is True and result.version == 1
        assert before != await service.get_prompt('concept') == '管理员新正文'
        added = await service.create_prompt(PromptCreate(name='admin-created', content='新模板'))
        assert added.is_customized is True


async def test_old_sqlite_schema_alter_is_idempotent_and_preserves_content(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "legacy.db"}')
    try:
        async with engine.begin() as connection:
            await connection.execute(text('CREATE TABLE chapter_outlines (id INTEGER PRIMARY KEY)'))
            await connection.execute(text('CREATE TABLE prompts (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, title VARCHAR(255), content TEXT NOT NULL, tags VARCHAR(255), updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)'))
            await connection.execute(text("INSERT INTO prompts(id,name,content) VALUES (1,'concept','旧库管理员定制')"))
        await _ensure_schema_updates(engine)
        await _ensure_schema_updates(engine)
        async with engine.connect() as connection:
            columns = await connection.run_sync(lambda sync: {row['name'] for row in inspect(sync).get_columns('prompts')})
            assert {'version', 'is_customized'} <= columns
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            record = await session.get(Prompt, 1)
            assert record.version == 0 and record.is_customized is False
            assert record.content == '旧库管理员定制'
            await _ensure_default_prompts(session)
            await session.commit()
            assert record.is_customized is True and record.content == '旧库管理员定制'
    finally:
        await engine.dispose()


def test_every_shipped_prompt_has_header_and_concept_baseline_matches_old_content():
    folder = Path(__file__).resolve().parents[1] / 'prompts'
    for path in folder.glob('*.md'):
        assert path.read_text().startswith('<!-- version: ')
        version, content = _prompt_file(path)
        assert version >= 1 and not content.startswith('<!-- version:')
    version, content = _prompt_file(folder / 'concept.md')
    assert version == 2 and '[已收集信息]' in content
    assert _prompt_file(folder / 'baselines/concept.v1.md')[0] == 1
