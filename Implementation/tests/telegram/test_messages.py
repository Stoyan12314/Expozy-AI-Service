"""
Tests for api.telegram.service.messaging.

The Telegram Bot API and settings are stubbed so no real HTTP calls
are made (NF.REQ6).
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helpers ───────────────────────────────────────────────────────────────────

CHAT_ID = 100
PREVIEW_URL = "/preview/abc123"
BASE_URL = "https://preview.expozy.com"
BUNDLE_ID = "bundle-xyz"


def _mock_settings(token: str = "test-token") -> MagicMock:
    s = MagicMock()
    s.telegram_bot_token = token
    return s


def _make_response(status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    return resp


# ── send_message ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_message_posts_to_telegram_api():
    # Arrange
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_make_response(200))

    with (
        patch("api.telegram.service.messaging.settings", _mock_settings()),
        patch("api.telegram.service.messaging.httpx.AsyncClient") as mock_async_client,
    ):
        mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_async_client.return_value.__aexit__ = AsyncMock(return_value=False)

        from api.telegram.service.messaging import send_message

        # Act
        await send_message(CHAT_ID, "Hello!")

    # Assert
    mock_client.post.assert_awaited_once()
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["chat_id"] == CHAT_ID
    assert payload["text"] == "Hello!"
    assert payload["parse_mode"] == "Markdown"


@pytest.mark.asyncio
async def test_send_message_returns_early_when_no_token():
    # Arrange
    mock_client = AsyncMock()

    with (
        patch("api.telegram.service.messaging.settings", _mock_settings(token="")),
        patch("api.telegram.service.messaging.httpx.AsyncClient") as mock_async_client,
    ):
        mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_async_client.return_value.__aexit__ = AsyncMock(return_value=False)

        from api.telegram.service.messaging import send_message

        # Act
        await send_message(CHAT_ID, "Hello!")

    # Assert
    mock_client.post.assert_not_called()


# ── notify_job_completed ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notify_job_completed_sends_simple_url_when_no_pages():
    # Arrange
    with patch("api.telegram.service.messaging.send_message", new=AsyncMock()) as mock_send:
        from api.telegram.service.messaging import notify_job_completed

        # Act
        await notify_job_completed(CHAT_ID, PREVIEW_URL, BASE_URL)

    # Assert
    mock_send.assert_awaited_once()
    text = mock_send.call_args[0][1]
    assert "✅" in text
    assert BASE_URL + PREVIEW_URL in text


# ── notify_job_failed ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notify_job_failed_sends_error_message():
    # Arrange
    with patch("api.telegram.service.messaging.send_message", new=AsyncMock()) as mock_send:
        from api.telegram.service.messaging import notify_job_failed

        # Act
        await notify_job_failed(CHAT_ID, "LLM timeout")

    # Assert
    mock_send.assert_awaited_once()
    text = mock_send.call_args[0][1]
    assert "❌" in text
    assert "LLM timeout" in text