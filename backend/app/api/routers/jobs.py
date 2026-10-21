from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from ...core.dependencies import get_current_user
from ...core.request_deadline import RequestDeadlineRoute, request_deadline
from ...schemas.job import JobRead
from ...schemas.user import UserInDB
from ...services.job_service import JobRunner, get_job_runner

router = APIRouter(prefix="/api/jobs", tags=["Jobs"], route_class=RequestDeadlineRoute)


@router.get("", response_model=List[JobRead])
@request_deadline
async def list_jobs(
    project_id: Optional[str] = None,
    active: bool = False,
    limit: int = Query(100, ge=1, le=100),
    current_user: UserInDB = Depends(get_current_user),
    runner: JobRunner = Depends(get_job_runner),
):
    return await runner.list(current_user.id, project_id=project_id, active=active, limit=limit)


@router.get("/{job_id}", response_model=JobRead)
@request_deadline
async def get_job(job_id: str, current_user: UserInDB = Depends(get_current_user), runner: JobRunner = Depends(get_job_runner)):
    return await runner.get(job_id, current_user.id)


@router.post("/{job_id}/cancel", response_model=JobRead)
@request_deadline
async def cancel_job(job_id: str, current_user: UserInDB = Depends(get_current_user), runner: JobRunner = Depends(get_job_runner)):
    return await runner.cancel(job_id, current_user.id)
