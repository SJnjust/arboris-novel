from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.dependencies import get_current_user
from ...core.request_deadline import RequestDeadlineRoute, request_deadline
from ...db.session import get_session
from ...models import NovelProject, NovelVolume
from ...schemas.job import JobSubmitted
from ...schemas.novel import Volume, VolumePatch, NovelProject as ProjectSchema
from ...schemas.user import UserInDB
from ...services.job_service import JobRunner, get_job_runner
from ...services.novel_service import NovelService
from ...services.volume_service import VolumeService, serialize_volumes

router = APIRouter(prefix="/api/novels", tags=["Volumes"], route_class=RequestDeadlineRoute)


async def require_owner(session, project_id, user_id):
    owner = await session.scalar(select(NovelProject.user_id).where(NovelProject.id == project_id))
    if owner is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if owner != user_id:
        raise HTTPException(status_code=403, detail="无权访问该项目")


@router.get("/{project_id}/volumes", response_model=list[Volume])
@request_deadline
async def list_volumes(project_id: str, session: AsyncSession = Depends(get_session), user: UserInDB = Depends(get_current_user)):
    project = await NovelService(session).ensure_project_owner(project_id, user.id)
    return serialize_volumes(project)


async def submit(runner, session, project_id, user_id, kind, target, payload, key):
    await require_owner(session, project_id, user_id)
    await session.rollback()
    job = await runner.submit(project_id=project_id, user_id=user_id, kind=kind, target=target, payload=payload, idempotency_key=key)
    return JobSubmitted(job_id=job.id)


@router.post("/{project_id}/volumes/outline-all", response_model=JobSubmitted, status_code=202)
@request_deadline
async def outline_all(project_id: str, session: AsyncSession = Depends(get_session), user: UserInDB = Depends(get_current_user),
                      runner: JobRunner = Depends(get_job_runner), idempotency_key: Optional[str] = Header(None, min_length=1, max_length=128)):
    return await submit(runner, session, project_id, user.id, "volumes_outline", "remaining", {}, idempotency_key)


@router.post("/{project_id}/volumes/smart-split", response_model=JobSubmitted, status_code=202)
@request_deadline
async def smart_split(project_id: str, session: AsyncSession = Depends(get_session), user: UserInDB = Depends(get_current_user),
                      runner: JobRunner = Depends(get_job_runner), idempotency_key: Optional[str] = Header(None, min_length=1, max_length=128)):
    return await submit(runner, session, project_id, user.id, "volume_smart_split", "plan", {}, idempotency_key)


@router.post("/{project_id}/volumes/{number}/outline", response_model=JobSubmitted, status_code=202)
@request_deadline
async def outline_volume(project_id: str, number: int, session: AsyncSession = Depends(get_session), user: UserInDB = Depends(get_current_user),
                         runner: JobRunner = Depends(get_job_runner), idempotency_key: Optional[str] = Header(None, min_length=1, max_length=128)):
    await require_owner(session, project_id, user.id)
    exists = await session.get(NovelVolume, (project_id, number))
    if not exists and number != 1:
        raise HTTPException(status_code=404, detail="卷不存在")
    return await submit(runner, session, project_id, user.id, "volume_outline", str(number), {"volume_number": number}, idempotency_key)


@router.patch("/{project_id}/volumes/{number}", response_model=ProjectSchema)
@request_deadline
async def patch_volume(project_id: str, number: int, payload: VolumePatch, session: AsyncSession = Depends(get_session),
                       user: UserInDB = Depends(get_current_user), runner: JobRunner = Depends(get_job_runner)):
    novel = NovelService(session)
    await novel.ensure_project_owner(project_id, user.id)
    await session.rollback()
    async with runner.edit_plan(project_id):
        project = await novel.ensure_project_owner(project_id, user.id)
        await VolumeService(session).patch(project, number, payload)
    return await novel.get_project_schema(project_id, user.id)
