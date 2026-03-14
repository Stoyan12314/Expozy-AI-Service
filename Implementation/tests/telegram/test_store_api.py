"""
Tests for api.telegram.service.store_api.

All HTTP calls and database sessions are stubbed so no real
network or PostgreSQL access is required (NF.REQ6).
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


CHAT_ID = 100
TELEGRAM_ID = 200


def _make_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = str(json_data or {})[:500]
    resp.json = MagicMock(return_value=json_data or {})
    return resp


def _make_http_client(response: MagicMock) -> MagicMock:
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    return client


def _patch_http(response: MagicMock):
    client = _make_http_client(response)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return patch("api.telegram.service.store_api.httpx.AsyncClient", return_value=ctx), client


# ── _api_login ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_login_returns_token_and_user_on_success():
    # Arrange
    response = _make_response(200, {"token": "tok-abc", "user": {"email": "owner@café.com"}})
    patcher, _ = _patch_http(response)

    with patcher:
        from api.telegram.service.store_api import _api_login

        # Act
        token, user, error = await _api_login("owner@café.com", "pass123", "mystore")

    # Assert
    assert token == "tok-abc"
    assert user == {"email": "owner@café.com"}
    assert error is None


@pytest.mark.asyncio
async def test_api_login_returns_error_on_network_exception():
    # Arrange
    with patch(
        "api.telegram.service.store_api.httpx.AsyncClient",
        side_effect=Exception("Connection refused"),
    ):
        from api.telegram.service.store_api import _api_login

        # Act
        token, user, error = await _api_login("a@b.com", "pass", "mystore")

    # Assert
    assert token == ""
    assert user is None
    assert "Connection refused" in error


# ── _do_newstore_background ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_do_newstore_background_saves_session_and_sends_success_message():
    # Arrange
    api_data = {
        "status": 1,
        "title": "mycafe",
        "token": "tok-store",
        "saas_key": "key-123",
        "url": "https://mycafe.expozy.com",
    }
    response = _make_response(200, api_data)
    patcher, _ = _patch_http(response)

    mock_db = AsyncMock()
    mock_db_ctx = MagicMock()
    mock_db_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patcher,
        patch("api.orchestrator.db.session.get_db_session", return_value=mock_db_ctx),
        patch("api.telegram.service.store_api.save_user_session", new=AsyncMock()) as mock_save,
        patch("api.telegram.service.store_api.send_message", new=AsyncMock()) as mock_send,
    ):
        from api.telegram.service.store_api import _do_newstore_background

        # Act
        await _do_newstore_background(
            telegram_id=TELEGRAM_ID, chat_id=CHAT_ID,
            title="mycafe", phone="+359888000000",
            email="owner@mycafe.com", password="s3cr3t",
        )

    # Assert
    mock_save.assert_awaited_once()
    mock_send.assert_awaited_once()
    text = mock_send.call_args[0][1]
    assert "✅" in text
    assert "mycafe" in text


@pytest.mark.asyncio
async def test_do_newstore_background_sends_error_message_on_exception():
    # Arrange
    with (
        patch(
            "api.telegram.service.store_api.httpx.AsyncClient",
            side_effect=Exception("timeout"),
        ),
        patch("api.telegram.service.store_api.send_message", new=AsyncMock()) as mock_send,
    ):
        from api.telegram.service.store_api import _do_newstore_background

        # Act
        await _do_newstore_background(
            telegram_id=TELEGRAM_ID, chat_id=CHAT_ID,
            title="store", phone="+359888000000",
            email="a@b.com", password="pass",
        )

    # Assert
    text = mock_send.call_args[0][1]
    assert "❌" in text
    assert "newstore" in text.lower() or "failed" in text.lower()