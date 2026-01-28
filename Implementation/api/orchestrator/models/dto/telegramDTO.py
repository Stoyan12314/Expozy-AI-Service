"""
Telegram DTOs for incoming webhook validation + helper accessors.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


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
