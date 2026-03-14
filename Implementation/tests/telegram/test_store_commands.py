"""
Tests for api.telegram.service.store_commands.

All persistence calls are replaced with AsyncMock stubs so no real
database access is required (NF.REQ6).
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


TELEGRAM_ID = 100


def _make_db() -> AsyncMock:
    return AsyncMock()


def _make_session(project: str, is_active: bool = False, url: str = "") -> MagicMock:
    s = MagicMock()
    s.project = project
    s.is_active = is_active
    s.project_url = url or f"https://{project}.expozy.com"
    return s


# ── list_stores ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_stores_returns_login_keyboard_when_no_sessions():
    # Arrange
    with patch("api.telegram.service.store_commands.get_all_sessions", new=AsyncMock(return_value=[])):
        from api.telegram.service.store_commands import list_stores

        # Act
        msg, kb = await list_stores(_make_db(), TELEGRAM_ID)

    # Assert
    assert "❌" in msg
    assert "newstore" in msg.lower()


@pytest.mark.asyncio
async def test_list_stores_lists_all_projects():
    # Arrange
    sessions = [_make_session("alpha"), _make_session("beta", is_active=True)]

    with patch("api.telegram.service.store_commands.get_all_sessions", new=AsyncMock(return_value=sessions)):
        from api.telegram.service.store_commands import list_stores

        # Act
        msg, _ = await list_stores(_make_db(), TELEGRAM_ID)

    # Assert
    assert "alpha" in msg
    assert "beta" in msg


# ── switch_store ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_switch_store_returns_success_when_found():
    # Arrange
    with patch("api.telegram.service.store_commands.set_active_session", new=AsyncMock(return_value=True)):
        from api.telegram.service.store_commands import switch_store

        # Act
        msg, _ = await switch_store(_make_db(), TELEGRAM_ID, "mystore")

    # Assert
    assert "✅" in msg
    assert "mystore" in msg


# ── handle_logout ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_logout_returns_error_when_no_active_session():
    # Arrange
    with patch("api.telegram.service.store_commands.get_user_session", new=AsyncMock(return_value=None)):
        from api.telegram.service.store_commands import handle_logout

        # Act
        msg, _ = await handle_logout(_make_db(), TELEGRAM_ID)

    # Assert
    assert "❌" in msg
    assert "No active" in msg


# ── get_session_status ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_session_status_returns_error_when_no_session():
    # Arrange
    with patch("api.telegram.service.store_commands.get_user_session", new=AsyncMock(return_value=None)):
        from api.telegram.service.store_commands import get_session_status

        # Act
        msg, _ = await get_session_status(_make_db(), TELEGRAM_ID)

    # Assert
    assert "❌" in msg
    assert "newstore" in msg.lower()


@pytest.mark.asyncio
async def test_get_session_status_shows_project_and_url():
    # Arrange
    session = _make_session("mystore", url="https://mystore.expozy.com")

    with (
        patch("api.telegram.service.store_commands.get_user_session", new=AsyncMock(return_value=session)),
        patch("api.telegram.service.store_commands.get_all_sessions", new=AsyncMock(return_value=[session])),
    ):
        from api.telegram.service.store_commands import get_session_status

        # Act
        msg, _ = await get_session_status(_make_db(), TELEGRAM_ID)

    # Assert
    assert "mystore" in msg
    assert "https://mystore.expozy.com" in msg