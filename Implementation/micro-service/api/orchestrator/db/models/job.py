# api/orchestrator/db/models/job.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from .enums import job_status_enum


class Job(Base):
    __tablename__ = "job"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    telegram_update_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("telegram_update.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        job_status_enum,
        default="queued",
        nullable=False,
        index=True,
    )

    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Result info (set by worker when finished)
    bundle_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True, unique=True)
    preview_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Failure info (set by worker on error)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Debug fields (used by worker)
    raw_ai_response: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    validation_errors: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    telegram_update: Mapped[Optional["TelegramUpdate"]] = relationship(
        "TelegramUpdate",
        back_populates="job",
    )

    attempts: Mapped[list["JobAttempt"]] = relationship(
        "JobAttempt",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobAttempt.attempt_no",
    )

    __table_args__ = (
        Index("ix_job_status_created_at", "status", "created_at"),
        UniqueConstraint("bundle_id", name="uq_job_bundle_id"),
    )
