# AIMETA P=小说API_项目和章节管理|R=小说CRUD_章节管理|NR=不含内容生成|E=route:GET_POST_/api/novels/*|X=http|A=小说CRUD_章节|D=fastapi,sqlalchemy|S=db|RD=./README.ai
import json
import logging
from contextlib import aclosing
from typing import Dict, List

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, UploadFile, status
from starlette.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete

from ...core.dependencies import get_current_user
from ...core.request_deadline import RequestDeadlineRoute, request_deadline
from .volumes import require_owner
from ...schemas.job import JobSubmitted
from ...models import NovelBlueprint, NovelConversation
from sqlalchemy import func, select
from ...db.session import get_session
from ...schemas.novel import (
    Blueprint,
    BlueprintGenerateRequest,
    BlueprintPatch,
    Chapter as ChapterSchema,
    ConverseRequest,
    ConverseResponse,
    NovelProject as NovelProjectSchema,
    NovelProjectSummary,
    NovelSectionResponse,
    NovelSectionType,
)
from ...schemas.user import UserInDB
from ...models.ai_job import AIJob
from ...services.job_service import JobRunner, get_job_runner
from ...services.concept_service import ConceptService
from ...services.import_service import ImportService
from ...services.novel_service import NovelService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/novels", tags=["Novels"], route_class=RequestDeadlineRoute)

@router.post("", response_model=NovelProjectSchema, status_code=status.HTTP_201_CREATED)
async def create_novel(
    title: str = Body(...),
    initial_prompt: str = Body(...),
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> NovelProjectSchema:
    """为当前用户创建一个新的小说项目。"""
    novel_service = NovelService(session)
    project = await novel_service.create_project(current_user.id, title, initial_prompt)
    logger.info("用户 %s 创建项目 %s", current_user.id, project.id)
    return await novel_service.get_project_schema(project.id, current_user.id)


@router.post("/import", response_model=Dict[str, str], status_code=status.HTTP_201_CREATED)
async def import_novel(
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> Dict[str, str]:
    """上传并导入小说文件。"""
    import_service = ImportService(session)
    project_id = await import_service.import_novel_from_file(current_user.id, file)
    logger.info("用户 %s 导入项目 %s", current_user.id, project_id)
    return {"id": project_id}


@router.get("", response_model=List[NovelProjectSummary])
async def list_novels(
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> List[NovelProjectSummary]:
    """列出用户的全部小说项目摘要信息。"""
    novel_service = NovelService(session)
    projects = await novel_service.list_projects_for_user(current_user.id)
    logger.info("用户 %s 获取项目列表，共 %s 个", current_user.id, len(projects))
    return projects


@router.get("/{project_id}", response_model=NovelProjectSchema)
async def get_novel(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> NovelProjectSchema:
    novel_service = NovelService(session)
    logger.info("用户 %s 查询项目 %s", current_user.id, project_id)
    return await novel_service.get_project_schema(project_id, current_user.id)


@router.get("/{project_id}/sections/{section}", response_model=NovelSectionResponse)
async def get_novel_section(
    project_id: str,
    section: NovelSectionType,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> NovelSectionResponse:
    novel_service = NovelService(session)
    logger.info("用户 %s 获取项目 %s 的 %s 区段", current_user.id, project_id, section)
    return await novel_service.get_section_data(project_id, current_user.id, section)


@router.get("/{project_id}/chapters/{chapter_number}", response_model=ChapterSchema)
async def get_chapter(
    project_id: str,
    chapter_number: int,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> ChapterSchema:
    novel_service = NovelService(session)
    logger.info("用户 %s 获取项目 %s 第 %s 章", current_user.id, project_id, chapter_number)
    return await novel_service.get_chapter_schema(project_id, current_user.id, chapter_number)


@router.delete("", status_code=status.HTTP_200_OK)
async def delete_novels(
    project_ids: List[str] = Body(...),
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
    runner: JobRunner = Depends(get_job_runner),
) -> Dict[str, str]:
    novel_service = NovelService(session)
    for project_id in project_ids:
        await novel_service.ensure_project_owner(project_id, current_user.id)
    await session.rollback()
    for job in await runner.list(current_user.id, active=True):
        if job.project_id in project_ids:
            await runner.cancel(job.id, current_user.id)
    await session.execute(delete(AIJob).where(AIJob.project_id.in_(project_ids)))
    await novel_service.delete_projects(project_ids, current_user.id)
    logger.info("用户 %s 删除项目 %s", current_user.id, project_ids)
    return {"status": "success", "message": f"成功删除 {len(project_ids)} 个项目"}


@router.post("/{project_id}/concept/converse", response_model=ConverseResponse)
async def converse_with_concept(
    project_id: str,
    request: ConverseRequest,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
) -> ConverseResponse:
    return await ConceptService(session).converse(project_id, current_user.id, request)


@router.post("/{project_id}/concept/converse/stream")
async def stream_concept(
    project_id: str,
    payload: ConverseRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
):
    await NovelService(session).ensure_project_owner(project_id, current_user.id)
    await session.rollback()

    async def body():
        async with aclosing(ConceptService(session).events(project_id, current_user.id, payload)) as events:
            async for item in events:
                if await request.is_disconnected():
                    return
                data = json.dumps(item["data"], ensure_ascii=False)
                yield f"event: {item['event']}\ndata: {data}\n\n"

    return StreamingResponse(body(), media_type="text/event-stream", headers={
        "X-Accel-Buffering": "no", "Cache-Control": "no-cache",
    })


@router.post("/{project_id}/blueprint/generate", response_model=JobSubmitted, status_code=202)
@request_deadline
async def generate_blueprint(
    project_id: str,
    payload: BlueprintGenerateRequest | None = Body(None),
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
    runner: JobRunner = Depends(get_job_runner),
    idempotency_key: str | None = Header(None, min_length=1, max_length=128),
) -> JobSubmitted:
    await require_owner(session, project_id, current_user.id)
    request = payload or BlueprintGenerateRequest()
    blueprint = await session.get(NovelBlueprint, project_id)
    resumable = request.resume and blueprint and (blueprint.generation_state or {}).get("core")
    if not resumable and not await session.scalar(select(func.count()).select_from(NovelConversation).where(NovelConversation.project_id == project_id)):
        raise HTTPException(status_code=400, detail="缺少对话历史，请先完成概念对话后再生成蓝图")
    await session.rollback()
    job = await runner.submit(project_id=project_id, user_id=current_user.id, kind="blueprint_generate", target="blueprint",
                              payload=request.model_dump(), idempotency_key=idempotency_key)
    return JobSubmitted(job_id=job.id)


@router.post("/{project_id}/blueprint/save", response_model=NovelProjectSchema)
async def save_blueprint(
    project_id: str,
    blueprint_data: Blueprint | None = Body(None),
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
    runner: JobRunner = Depends(get_job_runner),
) -> NovelProjectSchema:
    """保存蓝图信息，可用于手动覆盖自动生成结果。"""
    novel_service = NovelService(session)
    project = await novel_service.ensure_project_owner(project_id, current_user.id)

    if blueprint_data:
        await session.rollback()
        async with runner.edit_plan(project_id):
            await novel_service.replace_blueprint(project_id, blueprint_data)
            if blueprint_data.title:
                project = await novel_service.ensure_project_owner(project_id, current_user.id)
                project.title = blueprint_data.title
                await session.commit()
        logger.info("项目 %s 手动保存蓝图", project_id)
    else:
        logger.warning("项目 %s 保存蓝图时未提供蓝图数据", project_id)
        raise HTTPException(status_code=400, detail="缺少蓝图数据，请提供有效的蓝图内容")

    return await novel_service.get_project_schema(project_id, current_user.id)


@router.patch("/{project_id}/blueprint", response_model=NovelProjectSchema)
async def patch_blueprint(
    project_id: str,
    payload: BlueprintPatch,
    session: AsyncSession = Depends(get_session),
    current_user: UserInDB = Depends(get_current_user),
    runner: JobRunner = Depends(get_job_runner),
) -> NovelProjectSchema:
    """局部更新蓝图字段，对世界观或角色做微调。"""
    novel_service = NovelService(session)
    project = await novel_service.ensure_project_owner(project_id, current_user.id)

    update_data = payload.model_dump(exclude_unset=True)
    await session.rollback()
    async with runner.edit_plan(project_id):
        await novel_service.patch_blueprint(project_id, update_data)
    logger.info("项目 %s 局部更新蓝图字段：%s", project_id, list(update_data.keys()))
    return await novel_service.get_project_schema(project_id, current_user.id)
