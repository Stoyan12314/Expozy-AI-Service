"""
Pydantic v2 schemas needed for:
Telegram webhook -> create job -> publish to RabbitMQ -> respond immediately.

Keeps:
- TelegramUpdate parsing helpers (get_text/get_chat_id/get_user_id)
- WebhookResponse for FastAPI response model
- ErrorResponse for error responses
- JobQueueMessage for RabbitMQ payload
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# Telegram Update Schemas (incoming webhook validation)
# =============================================================================

class TelegramUser(BaseModel):
    id: int
    is_bot: bool = False
    first_name: str
    last_name: Optional[str] = None
    username: Optional[str] = None
    language_code: Optional[str] = None


class TelegramChat(BaseModel):
    id: int
    type: str  # private, group, supergroup, channel
    title: Optional[str] = None
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class TelegramMessage(BaseModel):
    message_id: int
    date: int
    chat: TelegramChat
    from_: Optional[TelegramUser] = Field(default=None, alias="from")
    text: Optional[str] = None
    caption: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class TelegramUpdate(BaseModel):
    update_id: int
    message: Optional[TelegramMessage] = None
    edited_message: Optional[TelegramMessage] = None
    channel_post: Optional[TelegramMessage] = None

    def get_message(self) -> Optional[TelegramMessage]:
        return self.message or self.edited_message or self.channel_post

    def get_text(self) -> Optional[str]:
        msg = self.get_message()
        return (msg.text or msg.caption) if msg else None

    def get_chat_id(self) -> Optional[int]:
        msg = self.get_message()
        return msg.chat.id if msg else None

    def get_user_id(self) -> Optional[int]:
        msg = self.get_message()
        return msg.from_.id if msg and msg.from_ else None


# =============================================================================
# Queue message schema (RabbitMQ payload)
# =============================================================================

class JobQueueMessage(BaseModel):
    job_id: UUID
    attempt: int = 1

    model_config = ConfigDict(frozen=True)


# =============================================================================
# API Response Schemas (what your FastAPI returns)
# =============================================================================

class WebhookResponse(BaseModel):
    ok: bool = True
    job_id: Optional[UUID] = None
    message: Optional[str] = None


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    request_id: Optional[str] = None
