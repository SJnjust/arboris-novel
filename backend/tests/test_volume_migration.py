from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.base import Base
from app.db.init_db import _ensure_schema_updates


async def test_old_schema_gets_volume_columns_and_table_without_data_loss(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "legacy.db"}')
    try:
        async with engine.begin() as connection:
            await connection.execute(text('CREATE TABLE chapter_outlines (id INTEGER PRIMARY KEY, chapter_number INTEGER, title TEXT, summary TEXT, metadata JSON)'))
            await connection.execute(text("INSERT INTO chapter_outlines VALUES (1,7,'旧章名','旧摘要','{}')"))
            await connection.execute(text('CREATE TABLE novel_blueprints (project_id VARCHAR(36) PRIMARY KEY, title TEXT)'))
            await connection.execute(text("INSERT INTO novel_blueprints VALUES ('legacy','旧蓝图')"))
            await connection.execute(text('CREATE TABLE prompts (id INTEGER PRIMARY KEY, version INTEGER, is_customized BOOLEAN)'))
            await connection.run_sync(Base.metadata.create_all)
        await _ensure_schema_updates(engine)
        await _ensure_schema_updates(engine)
        async with engine.connect() as connection:
            tables, outline_columns, blueprint_columns = await connection.run_sync(lambda sync: (
                inspect(sync).get_table_names(), {row['name'] for row in inspect(sync).get_columns('chapter_outlines')},
                {row['name'] for row in inspect(sync).get_columns('novel_blueprints')},
            ))
            assert 'novel_volumes' in tables
            assert 'volume_number' in outline_columns
            assert {'total_chapters', 'generation_state'} <= blueprint_columns
            row = (await connection.execute(text('SELECT chapter_number,title,summary,volume_number FROM chapter_outlines'))).one()
            assert tuple(row) == (7, '旧章名', '旧摘要', None)
            assert (await connection.execute(text('SELECT title,total_chapters FROM novel_blueprints'))).one() == ('旧蓝图', 0)
    finally:
        await engine.dispose()
