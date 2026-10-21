from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class JobSubmitted(BaseModel):
    job_id: str


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    kind: str
    target: str
    status: str
    stage: str
    progress: int
    payload: dict
    result: Optional[dict]
    error: Optional[dict]
    created_at: datetime
    updated_at: datetime
    heartbeat_at: datetime
