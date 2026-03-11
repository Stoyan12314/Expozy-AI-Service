from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, String, Text, DateTime, Boolean, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class TelegramSession(Base):
    __tablename__ = "telegram_sessions"
    __table_args__ = (
        UniqueConstraint("telegram_id", "project", name="uq_telegram_sessions_user_project"),
    )

    id:          Mapped[int]           = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int]           = mapped_column(BigInteger, nullable=False, index=True)
    project:     Mapped[str]           = mapped_column(String(255), nullable=False)
    token:       Mapped[str]           = mapped_column(Text, nullable=False)
    saas_key:    Mapped[str]           = mapped_column(String(255), nullable=False)
    project_url: Mapped[str]           = mapped_column(String(512), nullable=False)
    is_active:   Mapped[bool]          = mapped_column(Boolean, nullable=False, default=False)
    updated_at:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class TelegramLoginState(Base):
    __tablename__ = "telegram_login_state"

    telegram_id: Mapped[int]           = mapped_column(BigInteger, primary_key=True)
    step:        Mapped[str]           = mapped_column(String(64), nullable=False)
    project:     Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email:       Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone:       Mapped[Optional[str]] = mapped_column(String(50),  nullable=True)
    updated_at:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)