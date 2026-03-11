# api/orchestrator/db/models/telegram_update.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, DateTime, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class TelegramUpdate(Base):
    __tablename__ = "telegram_update"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    update_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, unique=True, index=True
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    raw_update: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    
    job: Mapped[Optional["Job"]] = relationship(
        "Job",
        back_populates="telegram_update",
        uselist=False,
    )

    __table_args__ = (
        UniqueConstraint("update_id", name="uq_telegram_update_update_id"),
        Index("ix_telegram_update_received_at", "received_at"),
    )
