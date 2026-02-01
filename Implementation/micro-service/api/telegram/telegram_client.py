"""
Telegram Bot API client for sending notifications.

Handles async message sending without blocking webhook responses.
Uses httpx for non-blocking HTTP requests.
"""

import asyncio
from typing import Optional, Any
from enum import Enum

import httpx

from shared.config import get_settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ParseMode(str, Enum):
    """Telegram message parse modes."""
    HTML = "HTML"
    MARKDOWN = "Markdown"
    MARKDOWN_V2 = "MarkdownV2"


class TelegramClient:
    """
    Async Telegram Bot API client.
    
    Provides methods for sending messages and notifications
    without blocking the calling code.
    """
    
    def __init__(self) -> None:
        self._settings = get_settings()
        self._base_url = f"https://api.telegram.org/bot{self._settings.telegram_bot_token}"
        self._client: Optional[httpx.AsyncClient] = None
    
    @property
    def is_configured(self) -> bool:
        """Check if bot token is configured."""
        return bool(self._settings.telegram_bot_token)
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client
    
    async def close(self) -> None:
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: Optional[ParseMode] = None,
        disable_notification: bool = False,
        reply_to_message_id: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        """
        Send a text message to a chat.
        
        Args:
            chat_id: Target chat ID
            text: Message text (max 4096 characters)
            parse_mode: Optional parse mode (HTML, Markdown, MarkdownV2)
            disable_notification: Send silently
            reply_to_message_id: Reply to specific message
            
        Returns:
            Telegram API response or None on error
        """
        if not self.is_configured:
            logger.warning("Telegram bot token not configured, skipping message")
            return None
        
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4096],  # Telegram limit
        }
        
        if parse_mode:
            payload["parse_mode"] = parse_mode.value
        if disable_notification:
            payload["disable_notification"] = True
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self._base_url}/sendMessage",
                json=payload,
            )
            
            if response.status_code != 200:
                logger.error(
                    "Telegram API error",
                    status_code=response.status_code,
                    response=response.text[:200],
                    chat_id=chat_id,
                )
                return None
            
            data = response.json()
            if not data.get("ok"):
                logger.error(
                    "Telegram API returned error",
                    error=data.get("description"),
                    chat_id=chat_id,
                )
                return None
            
            logger.debug("Message sent", chat_id=chat_id, message_id=data.get("result", {}).get("message_id"))
            return data.get("result")
            
        except httpx.TimeoutException:
            logger.warning("Telegram API timeout", chat_id=chat_id)
            return None
        except Exception as e:
            logger.error("Failed to send Telegram message", error=str(e), chat_id=chat_id)
            return None
    
    async def send_message_fire_and_forget(
        self,
        chat_id: int,
        text: str,
        **kwargs: Any,
    ) -> None:
        """
        Send a message without waiting for result.
        
        Creates a background task that doesn't block the caller.
        Errors are logged but not raised.
        """
        asyncio.create_task(
            self._send_message_safe(chat_id, text, **kwargs)
        )
    
    async def _send_message_safe(
        self,
        chat_id: int,
        text: str,
        **kwargs: Any,
    ) -> None:
        """Send message with error handling (for fire-and-forget)."""
        try:
            await self.send_message(chat_id, text, **kwargs)
        except Exception as e:
            logger.error(
                "Fire-and-forget message failed",
                error=str(e),
                chat_id=chat_id,
            )
    
    async def send_typing_action(self, chat_id: int) -> bool:
        """
        Send typing indicator to chat.
        
        Shows "typing..." status in Telegram client.
        """
        if not self.is_configured:
            return False
        
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self._base_url}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
            )
            return response.status_code == 200
        except Exception:
            return False
    
    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        parse_mode: Optional[ParseMode] = None,
    ) -> Optional[dict[str, Any]]:
        """
        Edit an existing message.
        
        Args:
            chat_id: Chat containing the message
            message_id: Message ID to edit
            text: New message text
            parse_mode: Optional parse mode
            
        Returns:
            Updated message or None on error
        """
        if not self.is_configured:
            return None
        
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text[:4096],
        }
        
        if parse_mode:
            payload["parse_mode"] = parse_mode.value
        
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self._base_url}/editMessageText",
                json=payload,
            )
            
            if response.status_code != 200:
                return None
            
            data = response.json()
            return data.get("result") if data.get("ok") else None
            
        except Exception as e:
            logger.error("Failed to edit message", error=str(e))
            return None


# Global client instance
_telegram_client: Optional[TelegramClient] = None


def get_telegram_client() -> TelegramClient:
    """Get or create Telegram client singleton."""
    global _telegram_client
    if _telegram_client is None:
        _telegram_client = TelegramClient()
    return _telegram_client


async def close_telegram_client() -> None:
    """Close Telegram client."""
    global _telegram_client
    if _telegram_client:
        await _telegram_client.close()
        _telegram_client = None


# Convenience functions for common operations

async def notify_job_started(chat_id: int, job_id: str) -> None:
    """Send 'Working on it...' notification (fire-and-forget)."""
    client = get_telegram_client()
    await client.send_message_fire_and_forget(
        chat_id,
        "🔄 Working on it... I'll send you the preview link when it's ready!",
    )


async def notify_job_completed(chat_id: int, preview_url: str, base_url: str = "") -> None:
    """Send job completion notification with preview link."""
    client = get_telegram_client()
    full_url = f"{base_url}{preview_url}" if base_url else preview_url
    
    await client.send_message(
        chat_id,
        f"✅ Your page is ready!\n\n🔗 Preview: {full_url}",
        parse_mode=ParseMode.HTML,
    )


async def notify_job_failed(chat_id: int, error_message: Optional[str] = None) -> None:
    """Send job failure notification."""
    client = get_telegram_client()
    
    text = "❌ Sorry, I couldn't generate your page."
    if error_message:
        text += f"\n\nError: {error_message[:200]}"
    text += "\n\nPlease try again with a different prompt."
    
    await client.send_message(chat_id, text)


async def send_telegram_message(chat_id: int, text: str, parse_mode: Optional[ParseMode] = ParseMode.MARKDOWN) -> None:
    """Send a message to a chat (convenience function)."""
    client = get_telegram_client()
    await client.send_message(chat_id, text, parse_mode=parse_mode)