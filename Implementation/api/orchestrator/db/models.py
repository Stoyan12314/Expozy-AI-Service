from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID, ENUM
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# Enums (keep create_type=False if Alembic already creates them)
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AttemptOutcome(str, Enum):
    SUCCESS = "success"
    FAIL = "fail"


job_status_enum = ENUM(
    "queued", "running", "completed", "failed",
    name="job_status",
    create_type=False,
)

attempt_outcome_enum = ENUM(
    "success", "fail",
    name="attempt_outcome",
    create_type=False,
)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    type_annotation_map = {
        dict[str, Any]: JSONB,
    }


# ---------------------------------------------------------------------------
# Telegram update (idempotency)
# ---------------------------------------------------------------------------

class TelegramUpdate(Base):
    __tablename__ = "telegram_update"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    update_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    raw_update: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # Optional 1:1 link to job
    job: Mapped[Optional["Job"]] = relationship(
        "Job",
        back_populates="telegram_update",
        uselist=False,
    )

    __table_args__ = (
        UniqueConstraint("update_id", name="uq_telegram_update_update_id"),
        Index("ix_telegram_update_received_at", "received_at"),
    )


# ---------------------------------------------------------------------------
# Job (request + result)
# ---------------------------------------------------------------------------

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

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

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


# ---------------------------------------------------------------------------
# Job attempt (each provider call)
# ---------------------------------------------------------------------------

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
