# AIMETA P=FastAPI应用入口_装配路由依赖和生命周期管理|R=应用启动_路由注册_中间件配置|NR=不含业务逻辑实现|E=uvicorn_app.main:app|X=http|A=FastAPI_app实例|D=fastapi,uvicorn|S=net,db|RD=./README.ai
"""FastAPI 应用入口，负责装配路由、依赖与生命周期管理。"""

import logging
from logging.config import dictConfig
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core.config import settings
from .db.init_db import init_db
from .services.prompt_service import PromptService
from .db.session import AsyncSessionLocal, engine
from .services.long_operation_service import register_long_operations
from .services.job_service import JobRunner
from .services.chapter_generation_service import ChapterGenerationService
from .services.volume_generation_service import VolumeGenerationService
from .services.job_service import PLANNING_KINDS
from .utils.llm_tool import close_openai_clients
from .api.routers import api_router


dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
            }
        },
        "loggers": {
            "backend": {
                "level": settings.logging_level,
                "handlers": ["console"],
                "propagate": False,
            },
            "app": {
                "level": settings.logging_level,
                "handlers": ["console"],
                "propagate": False,
            },
            "backend.app": {
                "level": settings.logging_level,
                "handlers": ["console"],
                "propagate": False,
            },
            "backend.api": {
                "level": settings.logging_level,
                "handlers": ["console"],
                "propagate": False,
            },
            "backend.services": {
                "level": settings.logging_level,
                "handlers": ["console"],
                "propagate": False,
            },
        },
        "root": {
            "level": "WARNING",
            "handlers": ["console"],
        },
    }
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 应用启动时初始化数据库，并预热提示词缓存
    await init_db()
    await app.state.job_runner.recover()
    async with AsyncSessionLocal() as session:
        prompt_service = PromptService(session)
        await prompt_service.preload()
    try:
        yield
    finally:
        try:
            await app.state.job_runner.shutdown()
        finally:
            try:
                await close_openai_clients()
            finally:
                await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    version="1.0.0",
    lifespan=lifespan,
)

app.state.job_runner = JobRunner(AsyncSessionLocal, user_limit=settings.ai_job_user_concurrency,
                               retention_days=settings.ai_job_retention_days)
app.state.job_runner.register("chapter_generate", ChapterGenerationService())
for kind in PLANNING_KINDS:
    if kind in ("outline_extend", "novel_import"):
        continue
    app.state.job_runner.register(kind, VolumeGenerationService(kind))

register_long_operations(app.state.job_runner)

# CORS 配置，生产环境建议改为具体域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


# 健康检查接口（用于 Docker 健康检查和监控）
@app.get("/health", tags=["Health"])
@app.get("/api/health", tags=["Health"])
async def health_check():
    """健康检查接口，返回应用状态。"""
    return {
        "status": "healthy",
        "app": settings.app_name,
        "version": "1.0.0",
    }
