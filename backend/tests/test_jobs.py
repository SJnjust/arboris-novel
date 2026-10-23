import asyncio
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.models import AIJob, Chapter, ChapterOutline


class GateHandler:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cleaned = []

    async def run(self, context):
        await context.advance("working", 25)
        await context.advance("working", 10)
        self.started.set()
        await self.release.wait()
        if context.payload.get("fail"):
            raise HTTPException(status_code=503, detail="模拟服务暂时不可用")
        return {"target": context.payload.get("chapter_number")}

    async def cleanup(self, context, status):
        self.cleaned.append((context.id, status))


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "cancelled"])
async def test_job_states_heartbeat_and_cleanup(client, auth, project, job_runner, wait_job, outcome):
    handler = GateHandler()
    job_runner.register("gate", handler)
    job_runner.heartbeat_seconds = 0.01
    job = await job_runner.submit(project_id=project, user_id=1, kind="gate", target="1",
                                  payload={"chapter_number": 1, "fail": outcome == "failed"})
    assert job.status == "queued"
    await asyncio.wait_for(handler.started.wait(), 1)
    running = await job_runner.get(job.id, 1)
    assert running.status == "running" and running.progress == 25
    assert job.id in job_runner.tasks
    for _ in range(100):
        current = await job_runner.get(job.id, 1)
        if current.heartbeat_at > running.heartbeat_at:
            break
        await asyncio.sleep(0.01)
    assert current.heartbeat_at > running.heartbeat_at
    if outcome == "cancelled":
        response = await client.post(f"/api/jobs/{job.id}/cancel", headers=auth)
        assert response.status_code == 200
    else:
        handler.release.set()
    result = await wait_job(job.id)
    assert result["status"] == outcome
    if outcome == "succeeded":
        assert result["progress"] == 100 and result["result"] == {"target": 1}
    elif outcome == "failed":
        assert result["error"] == {"status_code": 503, "detail": "模拟服务暂时不可用", "stage": "working", "step": "working"}
    assert handler.cleaned == ([] if outcome == "succeeded" else [(job.id, outcome)])
    task = job_runner.tasks.get(job.id)
    if task:
        await asyncio.wait_for(task, 1)
    assert job.id not in job_runner.tasks
    again = await client.post(f"/api/jobs/{job.id}/cancel", headers=auth)
    assert again.json()["status"] == outcome


async def test_submit_is_idempotent_conflicts_409_and_user_limit_429(client, auth, project, database, job_runner):
    handler = GateHandler()
    job_runner.register("chapter_generate", handler)
    async with database() as session:
        for number in [1, 2, 3]:
            session.add(ChapterOutline(project_id=project, chapter_number=number, title=str(number), summary="test"))
        await session.commit()

    async def submit(number, key):
        return await client.post(f"/api/writer/novels/{project}/chapters/generate", headers={**auth, "Idempotency-Key": key}, json={"chapter_number": number})

    first = await submit(1, "stable-key")
    assert first.status_code == 202
    first_id = first.json()["job_id"]
    await handler.started.wait()
    replay = await submit(1, "stable-key")
    assert replay.status_code == 202 and replay.json() == first.json()
    duplicate = await submit(1, "different-key")
    assert duplicate.status_code == 409 and duplicate.json()["detail"]["job_id"] == first_id
    second = await submit(2, "STABLE-KEY")
    assert second.status_code == 202
    third = await submit(3, "third-key")
    assert third.status_code == 429 and third.headers["retry-after"] == "5"
    assert len(await job_runner.list(1, active=True)) == 2
    await client.post(f"/api/jobs/{first_id}/cancel", headers=auth)
    assert (await submit(3, "third-key")).status_code == 202
    # 终态同一幂等键也不创建新任务。
    assert (await submit(1, "stable-key")).json()["job_id"] == first_id


async def test_job_ownership_and_active_filter(client, auth, password, project, job_runner):
    handler = GateHandler()
    job_runner.register("gate", handler)
    job = await job_runner.submit(project_id=project, user_id=1, kind="gate", target="1", payload={})
    assert (await client.get(f"/api/jobs/{job.id}")).status_code == 401
    token = (await client.post("/api/auth/token", data={"username": "other", "password": password})).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    assert (await client.get(f"/api/jobs/{job.id}", headers=other)).status_code == 404
    assert (await client.post(f"/api/jobs/{job.id}/cancel", headers=other)).status_code == 404
    assert (await client.get(f"/api/jobs?project_id={project}&active=1", headers=other)).json() == []
    listed = (await client.get(f"/api/jobs?project_id={project}&active=1", headers=auth)).json()
    assert [item["id"] for item in listed] == [job.id]
    await job_runner.cancel(job.id, 1)
    assert (await client.get("/api/jobs?active=1", headers=auth)).json() == []


async def test_startup_recovers_jobs_and_chapters_and_shutdown_cancels(database, project, job_runner, monkeypatch):
    import app.main as main
    now = datetime.utcnow()
    async with database() as session:
        for index, status in enumerate(["queued", "running", "succeeded"]):
            session.add(AIJob(id=str(uuid.uuid4()), project_id=project, user_id=1, kind="gate", target=str(index),
                              status=status, stage=status, progress=10, payload={}, idempotency_key=str(uuid.uuid4()),
                              created_at=now, updated_at=now, heartbeat_at=now))
        for number, status in enumerate(["generating", "evaluating", "selecting", "successful"], 1):
            session.add(Chapter(project_id=project, chapter_number=number, status=status))
        await session.commit()
    initializer = AsyncMock()
    monkeypatch.setattr(main, "init_db", initializer)
    monkeypatch.setattr(main, "AsyncSessionLocal", database)
    handler = GateHandler()
    job_runner.register("gate", handler)
    async with main.lifespan(main.app):
        initializer.assert_awaited_once()
        async with database() as session:
            records = (await session.scalars(select(AIJob))).all()
            assert sorted(job.status for job in records) == ["failed", "failed", "succeeded"]
            assert all("服务重启" in job.error["detail"] for job in records if job.status == "failed")
            chapters = (await session.scalars(select(Chapter).order_by(Chapter.chapter_number))).all()
            assert [chapter.status for chapter in chapters] == ["failed", "failed", "failed", "successful"]
        running = await job_runner.submit(project_id=project, user_id=1, kind="gate", target="9", payload={})
        await handler.started.wait()
    assert (await job_runner.get(running.id, 1)).status == "cancelled"
    assert not job_runner.tasks


async def test_retention_preserves_active_jobs_and_sqlite_is_wal(database, project, job_runner):
    old = datetime.utcnow() - timedelta(days=8)
    async with database() as session:
        for status in ["succeeded", "running"]:
            session.add(AIJob(id=str(uuid.uuid4()), project_id=project, user_id=1, kind="gate", target=status,
                              status=status, stage=status, progress=10, payload={}, idempotency_key=status,
                              created_at=old, updated_at=old, heartbeat_at=old))
        await session.commit()
        assert (await session.scalar(text("PRAGMA journal_mode"))).lower() == "wal"
        assert await session.scalar(text("PRAGMA busy_timeout")) == 30000
    await job_runner._prune(force=True)
    assert [job.status for job in await job_runner.list(1)] == ["running"]


async def test_owned_maintenance_has_strong_references_and_stops(job_runner):
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def maintenance():
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            stopped.set()

    job_runner.own(maintenance())
    await started.wait()
    assert job_runner.owned_tasks
    await job_runner.shutdown()
    assert stopped.is_set() and not job_runner.owned_tasks


async def test_deleting_project_cancels_work_before_removing_job_records(client, auth, project, job_runner, database):
    handler = GateHandler()
    job_runner.register("gate", handler)
    job = await job_runner.submit(project_id=project, user_id=1, kind="gate", target="1", payload={})
    await handler.started.wait()
    response = await client.request("DELETE", "/api/novels", headers=auth, json=[project])
    assert response.status_code == 200
    assert handler.cleaned == [(job.id, "cancelled")]
    assert (await client.get(f"/api/jobs/{job.id}", headers=auth)).status_code == 404
    async with database() as session:
        assert (await session.scalars(select(AIJob))).all() == []
