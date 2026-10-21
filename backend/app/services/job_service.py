"""单进程任务调度：持久状态、短事务、强引用和协作取消。"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from ..models.ai_job import AIJob
from ..models.novel import Chapter

logger = logging.getLogger(__name__)
ACTIVE = ("queued", "running")
TERMINAL = ("succeeded", "failed", "cancelled")


class JobContext:
    def __init__(self, runner, job):
        self.runner = runner
        self.id = job.id
        self.project_id = job.project_id
        self.user_id = job.user_id
        self.payload = job.payload
        self.session_factory = runner.session_factory
        self.cancelled = False

    def check_cancelled(self):
        task = asyncio.current_task()
        if self.cancelled or task and task.cancelling():
            raise asyncio.CancelledError

    async def advance(self, stage, progress):
        self.check_cancelled()
        await self.runner._update(self.id, stage=stage, progress=max(0, min(99, progress)))
        self.check_cancelled()


class JobRunner:
    def __init__(self, session_factory, *, user_limit=2, heartbeat_seconds=15, retention_days=7):
        self.session_factory = session_factory
        self.user_limit = user_limit
        self.heartbeat_seconds = heartbeat_seconds
        self.retention_days = retention_days
        self.handlers = {}
        self.tasks = {}
        self.contexts = {}
        self.owned_tasks = set()
        self._admission = asyncio.Lock()
        self._writes = asyncio.Lock()
        self._closing = False
        self._last_cleanup = datetime.min
        self._owned_gate = asyncio.Semaphore(2)

    def register(self, kind, handler):
        self.handlers[kind] = handler

    async def get(self, job_id, user_id):
        async with self.session_factory() as session:
            job = await session.get(AIJob, job_id)
            if not job or job.user_id != user_id:
                raise HTTPException(status_code=404, detail="任务不存在")
            return job

    async def list(self, user_id, *, project_id=None, active=False, limit=100):
        query = select(AIJob).where(AIJob.user_id == user_id)
        if project_id:
            query = query.where(AIJob.project_id == project_id)
        if active:
            query = query.where(AIJob.status.in_(ACTIVE))
        async with self.session_factory() as session:
            result = await session.scalars(query.order_by(AIJob.created_at.desc(), AIJob.id).limit(limit))
            return result.all()

    async def submit(self, *, project_id, user_id, kind, target, payload, idempotency_key=None):
        key = idempotency_key or str(uuid.uuid4())
        if not key.strip() or len(key) > 128:
            raise HTTPException(status_code=422, detail="幂等键须为 1–128 个字符")
        if kind not in self.handlers:
            raise ValueError(f"未注册任务类型: {kind}")
        async with self._admission:
            if self._closing:
                raise HTTPException(status_code=503, detail="服务正在退出，请稍后重试")
            await self._prune()
            async with self.session_factory() as session:
                original = await session.scalar(select(AIJob).where(AIJob.user_id == user_id, AIJob.idempotency_key == key))
                if original:
                    return original
                conflict = await session.scalar(select(AIJob).where(
                    AIJob.project_id == project_id, AIJob.kind == kind,
                    AIJob.target == target, AIJob.status.in_(ACTIVE),
                ))
                if conflict:
                    raise HTTPException(status_code=409, detail={"message": "该目标已有进行中的任务", "job_id": conflict.id})
                count = await session.scalar(select(func.count()).select_from(AIJob).where(AIJob.user_id == user_id, AIJob.status.in_(ACTIVE)))
                if count >= self.user_limit:
                    raise HTTPException(status_code=429, detail="进行中的任务已达上限，请等待完成后重试", headers={"Retry-After": "5"})
                now = datetime.utcnow()
                job = AIJob(id=str(uuid.uuid4()), project_id=project_id, user_id=user_id, kind=kind,
                            target=target, status="queued", stage="queued", progress=0,
                            payload=payload, idempotency_key=key, heartbeat_at=now, created_at=now, updated_at=now)
                session.add(job)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    original = await session.scalar(select(AIJob).where(AIJob.user_id == user_id, AIJob.idempotency_key == key))
                    if original:
                        return original
                    raise
            context = JobContext(self, job)
            self.contexts[job.id] = context
            task = asyncio.create_task(self._run(job, context), name=f"ai-job:{job.id}")
            self.tasks[job.id] = task
            task.add_done_callback(lambda done: self._finished(job.id, done))
            return job

    def _finished(self, job_id, task):
        self.tasks.pop(job_id, None)
        self.contexts.pop(job_id, None)
        if not task.cancelled() and task.exception():
            logger.error("后台任务异常退出: job=%s", job_id, exc_info=task.exception())

    async def _update(self, job_id, **values):
        async with self._writes:
            async with self.session_factory() as session:
                job = await session.get(AIJob, job_id)
                if not job or job.status in TERMINAL:
                    return
                if "progress" in values:
                    values["progress"] = max(job.progress, values["progress"])
                for key, value in values.items():
                    setattr(job, key, value)
                job.updated_at = job.heartbeat_at = datetime.utcnow()
                await session.commit()

    async def _heartbeat(self, job_id):
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            try:
                await self._update(job_id)
            except Exception:
                logger.warning("任务心跳写入失败，将在下次重试: job=%s", job_id, exc_info=True)

    async def _cleanup(self, kind, context, status):
        handler = self.handlers.get(kind)
        if handler and hasattr(handler, "cleanup"):
            try:
                await handler.cleanup(context, status)
            except Exception:
                logger.exception("任务状态清理失败: job=%s", context.id)

    async def _run(self, job, context):
        heartbeat = None
        try:
            await self._update(job.id, status="running", stage="starting")
            heartbeat = asyncio.create_task(self._heartbeat(job.id), name=f"heartbeat:{job.id}")
            context.check_cancelled()
            result = await self.handlers[job.kind].run(context)
            context.check_cancelled()
            await self._update(job.id, status="succeeded", stage="succeeded", progress=100, result=result)
        except asyncio.CancelledError:
            context.cancelled = True
            await self._cleanup(job.kind, context, "cancelled")
            await self._update(job.id, status="cancelled", stage="cancelled")
        except Exception as error:
            logger.exception("AI 任务失败: job=%s kind=%s", job.id, job.kind)
            await self._cleanup(job.kind, context, "failed")
            detail = error.detail if isinstance(error, HTTPException) else "任务执行失败，请稍后重试"
            code = error.status_code if isinstance(error, HTTPException) else 500
            await self._update(job.id, status="failed", stage="failed", error={"detail": detail, "status_code": code})
        finally:
            if heartbeat:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    async def cancel(self, job_id, user_id):
        job = await self.get(job_id, user_id)
        if job.status in TERMINAL:
            return job
        context = self.contexts.get(job_id) or JobContext(self, job)
        context.cancelled = True
        task = self.tasks.get(job_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # 排队协程尚未开始就被取消时，_run 的 finally 不会执行。
        current = await self.get(job_id, user_id)
        if current.status in ACTIVE:
            await self._cleanup(job.kind, context, "cancelled")
            await self._update(job_id, status="cancelled", stage="cancelled")
        return await self.get(job_id, user_id)

    def own(self, coroutine):
        """持有编辑后维护任务；异常可见，退出时与 AI 任务一起取消。"""
        if self._closing or len(self.owned_tasks) >= 64:
            coroutine.close()
            logger.warning("编辑后维护队列繁忙，未启动额外任务")
            return
        async def execute():
            started = False
            try:
                async with self._owned_gate:
                    started = True
                    await coroutine
            finally:
                if not started:
                    coroutine.close()

        task = asyncio.create_task(execute(), name="chapter-maintenance")
        self.owned_tasks.add(task)

        def done(finished):
            self.owned_tasks.discard(finished)
            coroutine.close()
            if not finished.cancelled() and finished.exception():
                logger.error("编辑后维护失败", exc_info=finished.exception())

        task.add_done_callback(done)

    async def recover(self):
        self._closing = False
        async with self.session_factory() as session:
            now = datetime.utcnow()
            await session.execute(update(AIJob).where(AIJob.status.in_(ACTIVE)).values(
                status="failed", stage="interrupted", updated_at=now, heartbeat_at=now,
                error={"detail": "服务重启，任务已中断，请重新提交", "status_code": 503},
            ))
            await session.execute(update(Chapter).where(Chapter.status.in_(("generating", "evaluating", "selecting"))).values(status="failed"))
            await session.commit()
        await self._prune(force=True)

    async def _prune(self, force=False):
        now = datetime.utcnow()
        if not force and now - self._last_cleanup < timedelta(hours=1):
            return
        async with self.session_factory() as session:
            await session.execute(delete(AIJob).where(AIJob.status.in_(TERMINAL), AIJob.updated_at < now - timedelta(days=self.retention_days)))
            await session.commit()
        self._last_cleanup = now

    async def shutdown(self):
        self._closing = True
        contexts = list(self.contexts.items())
        running = list(self.tasks.values()) + list(self.owned_tasks)
        for _, context in contexts:
            context.cancelled = True
        for task in running:
            if not task.done():
                task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        # 包括尚未开始执行、没有机会进入 _run 的 queued 任务。
        for job_id, context in contexts:
            try:
                await self.cancel(job_id, context.user_id)
            except HTTPException as error:
                if error.status_code != 404:
                    raise


def get_job_runner(request: Request) -> JobRunner:
    return request.app.state.job_runner
