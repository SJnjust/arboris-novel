"""可恢复的 AI 后台任务记录，不保存模型凭据。"""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.mysql import VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class AIJob(Base):
    __tablename__ = "ai_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_ai_job_idempotency"),
        Index("ix_ai_job_active_target", "project_id", "kind", "target", "status"),
        Index("ix_ai_job_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("novel_projects.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    stage: Mapped[str] = mapped_column(String(64), default="queued", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[Optional[dict]] = mapped_column(JSON)
    error: Mapped[Optional[dict]] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(
        String(128).with_variant(VARCHAR(128, charset="utf8mb4", collation="utf8mb4_bin"), "mysql"),
        nullable=False,
    )
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
