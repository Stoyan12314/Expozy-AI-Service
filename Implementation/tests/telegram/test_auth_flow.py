"""
Tests for the Telegram authentication flow (start_login_flow,
start_newstore_flow, handle_auth_step).

All database calls are replaced with AsyncMock stubs so no real
PostgreSQL connection is required (NF.REQ6).
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from api.telegram.service.auth_wizard import (
    start_login_flow,
    start_newstore_flow,
    handle_auth_step,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

TELEGRAM_ID = 123456789
CHAT_ID = 123456789


def _make_db() -> AsyncMock:
    return AsyncMock()


def _make_state(step: str, **kwargs) -> MagicMock:
    state = MagicMock()
    state.step = step
    state.project = kwargs.get("project", "teststore")
    state.email = kwargs.get("email", "user@example.com")
    state.phone = kwargs.get("phone", "+359888000000")
    return state


# ── start_newstore_flow ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_newstore_flow_clears_and_sets_state():
    # Arrange
    db = _make_db()

    with (
        patch("api.telegram.service.auth_wizard.clear_login_state", new=AsyncMock()) as mock_clear,
        patch("api.telegram.service.auth_wizard.set_login_state", new=AsyncMock()) as mock_set,
    ):
        # Act
        message, keyboard = await start_newstore_flow(db, TELEGRAM_ID)

        # Assert
        mock_clear.assert_awaited_once_with(db, TELEGRAM_ID)
        mock_set.assert_awaited_once_with(db, TELEGRAM_ID, step="newstore:title")
        assert "store name" in message.lower() or "storefront" in message.lower()


# ── start_login_flow ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_login_flow_returns_prompt_when_no_session():
    # Arrange
    db = _make_db()

    with (
        patch("api.telegram.service.auth_wizard.get_user_session", new=AsyncMock(return_value=None)),
        patch("api.telegram.service.auth_wizard.clear_login_state", new=AsyncMock()),
        patch("api.telegram.service.auth_wizard.set_login_state", new=AsyncMock()) as mock_set,
    ):
        # Act
        message, _ = await start_login_flow(db, TELEGRAM_ID)

        # Assert
        mock_set.assert_awaited_once_with(db, TELEGRAM_ID, step="login:project")
        assert "store name" in message.lower()


# ── handle_auth_step — guard clauses ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_auth_step_returns_none_when_no_state():
    # Arrange
    db = _make_db()

    with patch("api.telegram.service.auth_wizard.get_login_state", new=AsyncMock(return_value=None)):
        # Act
        result = await handle_auth_step(
            db, TELEGRAM_ID, "hello", chat_id=CHAT_ID, background_tasks=MagicMock()
        )

        # Assert
        assert result is None


# ── handle_auth_step — newstore wizard ───────────────────────────────────────

@pytest.mark.asyncio
async def test_newstore_title_step_advances_to_email():
    # Arrange
    db = _make_db()

    with (
        patch("api.telegram.service.auth_wizard.get_login_state", new=AsyncMock(return_value=_make_state("newstore:title"))),
        patch("api.telegram.service.auth_wizard.set_login_state", new=AsyncMock()) as mock_set,
    ):
        # Act
        message, _ = await handle_auth_step(
            db, TELEGRAM_ID, "MyCafe2026", chat_id=CHAT_ID, background_tasks=MagicMock()
        )

        # Assert
        mock_set.assert_awaited_once_with(db, TELEGRAM_ID, step="newstore:email", project="mycafe2026")
        assert "email" in message.lower()


# ── handle_auth_step — login wizard ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_password_step_saves_session_on_success():
    # Arrange
    db = _make_db()
    state = _make_state("login:password", project="mystore", email="owner@mystore.com")
    existing = MagicMock()
    existing.saas_key = "key-abc-123"
    existing.project_url = "https://mystore.expozy.com"

    with (
        patch("api.telegram.service.auth_wizard.get_login_state", new=AsyncMock(return_value=state)),
        patch("api.telegram.service.auth_wizard._api_login", new=AsyncMock(return_value=("tok123", {"email": "owner@mystore.com"}, None))),
        patch("api.telegram.service.auth_wizard.get_session_by_project", new=AsyncMock(return_value=existing)),
        patch("api.telegram.service.auth_wizard.clear_login_state", new=AsyncMock()),
        patch("api.telegram.service.auth_wizard.save_user_session", new=AsyncMock()) as mock_save,
    ):
        # Act
        message, _ = await handle_auth_step(
            db, TELEGRAM_ID, "correctpass", chat_id=CHAT_ID, background_tasks=MagicMock()
        )

        # Assert
        mock_save.assert_awaited_once()
        assert "✅" in message
        assert "mystore" in message