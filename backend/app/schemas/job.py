from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_serializer


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

    @field_serializer("payload")
    def public_payload(self, payload):
        # 上传正文保留在执行状态中供 worker 使用，不随轮询重复传回浏览器。
        if self.kind == "novel_import":
            return {"filename": payload.get("filename", "")}
        return payload
