"""后台模型策略通过 contextvars 传递给并行分支，不影响前台 SSE 的时限。"""
from contextlib import contextmanager
from contextvars import ContextVar

from .config import settings

_IN_JOB = ContextVar("llm_in_job", default=False)


@contextmanager
def llm_job_context():
    token = _IN_JOB.set(True)
    try:
        yield
    finally:
        _IN_JOB.reset(token)


def generation_deadline(timeout):
    return settings.llm_job_deadline_seconds if _IN_JOB.get() else min(timeout, settings.llm_deadline_seconds)


def read_timeout():
    return settings.llm_job_read_timeout_seconds if _IN_JOB.get() else 90
