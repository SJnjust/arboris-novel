"""Isolated SQLite API fixtures; no dotenv reads or outgoing network calls."""

import json
import os
import secrets
import socket
import tempfile
from collections import defaultdict, deque
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from pydantic_settings.sources import DotEnvSettingsSource
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


_temporary = tempfile.TemporaryDirectory(prefix="arboris-tests-")
_password = secrets.token_urlsafe(18)
# Set these before importing app: even its unused default engine is temporary.
os.environ.update(
    SECRET_KEY=secrets.token_urlsafe(32),
    DB_PROVIDER="sqlite",
    SQLITE_DB_PATH=f"{_temporary.name}/unused.db",
    DATABASE_URL="",
    DEBUG="false",
    LOGGING_LEVEL="WARNING",
    VECTOR_DB_URL="",
    OPENAI_API_KEY="offline-test-placeholder",
    WRITER_CHAPTER_VERSION_COUNT="2",
    AI_JOB_USER_CONCURRENCY="2",
    AI_JOB_RETENTION_DAYS="7",
    CHAPTER_SUMMARY_BACKFILL_LIMIT="5",
    LLM_DEADLINE_SECONDS="600",
    HTTP_REQUEST_DEADLINE_SECONDS="90",
)
with patch.object(DotEnvSettingsSource, "_read_env_files", return_value={}):
    from app.main import app

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import configure_sqlite, get_session
from app.models import Prompt, User
from app.services import llm_service, prompt_service
from app.services.chapter_ingest_service import ChapterIngestionService
from app.services.chapter_generation_service import ChapterGenerationService
from app.services.job_service import JobRunner, PLANNING_KINDS
from app.services.volume_generation_service import VolumeGenerationService

REAL_INGEST_CHAPTER = ChapterIngestionService.ingest_chapter
REAL_RESOLVE_CONFIG = llm_service.LLMService._resolve_llm_config


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    original = socket.socket.connect

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("Tests must not connect to external services")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect)


@pytest_asyncio.fixture
async def database(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    configure_sqlite(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add_all([
            User(id=1, username="reader", hashed_password=hash_password(_password)),
            User(id=2, username="other", hashed_password=hash_password(_password)),
        ])
        for name in ["concept", "screenwriting", "volume_planning", "volume_outline", "outline_generation", "chapter_plan", "writing_v2", "editor_review", "evaluation", "extraction"]:
            session.add(Prompt(name=name, content=f"fixture:{name}"))
        await session.commit()
    monkeypatch.setattr(prompt_service, "_CACHE", {})
    monkeypatch.setattr(prompt_service, "_LOADED", False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def job_runner(database, monkeypatch):
    runner = JobRunner(database)
    runner.register("chapter_generate", ChapterGenerationService())
    for kind in PLANNING_KINDS:
        runner.register(kind, VolumeGenerationService(kind))
    monkeypatch.setattr(app.state, "job_runner", runner)
    try:
        yield runner
    finally:
        await runner.shutdown()


@pytest_asyncio.fixture
async def client(database, job_runner):
    async def session_dependency():
        async with database() as session:
            yield session

    app.dependency_overrides[get_session] = session_dependency
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def wait_job(client, auth):
    async def wait(job_id, *, timeout=10):
        import asyncio
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            response = await client.get(f"/api/jobs/{job_id}", headers=auth)
            assert response.status_code == 200
            result = response.json()
            if result["status"] in ("succeeded", "failed", "cancelled"):
                return result
            await asyncio.sleep(0.01)
        raise AssertionError("Job did not reach a terminal state")

    return wait


@pytest.fixture
def password():
    return _password


@pytest_asyncio.fixture
async def auth(client, password):
    response = await client.post("/api/auth/token", data={"username": "reader", "password": password})
    assert response.status_code == 200
    class Credentials(dict):
        def __repr__(self):
            return "<test authorization headers>"

    return Credentials(Authorization=f"Bearer {response.json()['access_token']}")


@pytest.fixture
def blueprint():
    return {
        "title": "旧馆的秘密", "genre": "悬疑", "full_synopsis": "一场关于失踪目录的调查。",
        "world_setting": {"city": "山城"},
        "characters": [{"name": "林川", "identity": "调查员", "habit": "记录线索"}, {"name": "许宁"}],
        "relationships": [{"character_from": "林川", "character_to": "许宁", "description": "同事"}],
        "chapter_outline": [{"chapter_number": 1, "title": "旧馆", "summary": "林川进入旧馆调查。"}],
    }


@pytest_asyncio.fixture
async def project(client, auth):
    response = await client.post("/api/novels", headers=auth, json={"title": "待命名", "initial_prompt": "一座神秘的旧馆"})
    assert response.status_code == 201
    return response.json()["id"]


@pytest_asyncio.fixture
async def ready_project(client, auth, project, blueprint):
    response = await client.post(f"/api/novels/{project}/blueprint/save", headers=auth, json=blueprint)
    assert response.status_code == 200
    return project


class ScriptedLLM:
    def __init__(self):
        self.responses = defaultdict(deque)
        self.calls = []
        self.unexpected = []

    def reply(self, prompt, content, *, finish_reason="stop", before=None):
        text = json.dumps(content, ensure_ascii=False) if isinstance(content, (dict, list)) else content
        self.responses[prompt].append((text, finish_reason, before))

    async def stream(self, **kwargs):
        name = kwargs["messages"][0].content.removeprefix("fixture:").splitlines()[0]
        self.calls.append((name, kwargs))
        if not self.responses[name]:
            self.unexpected.append(name)
            raise AssertionError(f"Unscripted LLM call: {name}")
        text, reason, before = self.responses[name].popleft()
        if before:
            await before()
        middle = len(text) // 2
        yield {"content": text[:middle]}
        yield {"content": text[middle:], "finish_reason": reason}


@pytest.fixture(autouse=True)
def llm(monkeypatch):
    script = ScriptedLLM()

    class Client:
        def __init__(self, **kwargs):
            pass

        def stream_chat(self, **kwargs):
            return script.stream(**kwargs)

    monkeypatch.setattr(llm_service, "LLMClient", Client)
    monkeypatch.setattr(llm_service.LLMService, "_resolve_llm_config", AsyncMock(return_value={"api_key": "offline", "model": "fixture-model"}))
    monkeypatch.setattr(ChapterIngestionService, "ingest_chapter", AsyncMock())
    yield script
    assert not script.unexpected, "Every LLM interaction must be explicitly scripted"
    assert not any(script.responses.values()), "Scripted LLM replies were not consumed"


def pytest_sessionfinish():
    _temporary.cleanup()
