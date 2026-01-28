# api/orchestrator/db/models/job_attempt.py
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from .enums import attempt_outcome_enum


class JobAttempt(Base):
    __tablename__ = "job_attempt"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("job.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)

    provider_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    outcome: Mapped[Optional[str]] = mapped_column(attempt_outcome_enum, nullable=True)

    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    provider_status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    job: Mapped["Job"] = relationship("Job", back_populates="attempts")

    __table_args__ = (
        UniqueConstraint("job_id", "attempt_no", name="uq_job_attempt_job_id_attempt_no"),
        Index("ix_job_attempt_job_id_attempt_no", "job_id", "attempt_no"),
    )
