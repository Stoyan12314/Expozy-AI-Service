"""
Tests for handle_telegram_update (Telegram webhook dispatcher).

All external boundaries are replaced with AsyncMock stubs so no real
database, RabbitMQ, or Telegram API calls are made (NF.REQ6).
Verifies F.REQ2, F.REQ3, F.REQ4, and NF.REQ1 (no AI call inside webhook).
"""

from __future__ import annotations

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from api.telegram.service.handler import handle_telegram_update


# ── Helpers ───────────────────────────────────────────────────────────────────

CHAT_ID = 100
USER_ID = 200
JOB_ID = uuid.uuid4()


def _make_update(text: str) -> MagicMock:
    update = MagicMock()
    update.get_text.return_value = text
    update.get_chat_id.return_value = CHAT_ID
    update.get_user_id.return_value = USER_ID
    return update


def _make_db() -> AsyncMock:
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


def _make_background_tasks() -> MagicMock:
    bt = MagicMock()
    bt.add_task = MagicMock()
    return bt


# ── Ignored updates ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ignores_update_with_no_text():
    # Arrange
    update = MagicMock()
    update.get_text.return_value = None
    update.get_chat_id.return_value = CHAT_ID
    update.get_user_id.return_value = USER_ID

    # Act
    result = await handle_telegram_update(
        db=_make_db(), raw_update={}, update=update,
        background_tasks=_make_background_tasks(),
    )

    # Assert
    assert result == {"ok": True, "message": "Ignored"}


# ── Simple commands ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/start", "/help"])
async def test_simple_commands_enqueue_send_message(command):
    # Arrange
    bt = _make_background_tasks()

    with patch("api.telegram.service.handler.send_message", new=AsyncMock()):
        # Act
        result = await handle_telegram_update(
            db=_make_db(), raw_update={}, update=_make_update(command),
            background_tasks=bt,
        )

    # Assert
    assert result == {"ok": True}
    bt.add_task.assert_called_once()


@pytest.mark.asyncio
async def test_newstore_command_starts_newstore_flow():
    # Arrange
    bt = _make_background_tasks()

    with (
        patch("api.telegram.service.handler.start_newstore_flow",
              new=AsyncMock(return_value=("enter name", {}))) as mock_flow,
        patch("api.telegram.service.handler.send_message", new=AsyncMock()),
    ):
        # Act
        result = await handle_telegram_update(
            db=_make_db(), raw_update={}, update=_make_update("/newstore"),
            background_tasks=bt,
        )

    # Assert
    assert result == {"ok": True}
    mock_flow.assert_awaited_once()


# ── /prompt — NF.REQ1: no AI call inside webhook ─────────────────────────────

@pytest.mark.asyncio
async def test_prompt_returns_no_update_id_when_db_id_is_none():
    # Arrange
    with (
        patch("api.telegram.service.handler.handle_auth_step",
              new=AsyncMock(return_value=None)),
        patch("api.telegram.service.handler.get_user_session",
              new=AsyncMock(return_value=MagicMock(saas_key="key123"))),
    ):
        # Act
        result = await handle_telegram_update(
            db=_make_db(), raw_update={}, update=_make_update("/prompt build a café site"),
            background_tasks=_make_background_tasks(),
            telegram_update_db_id=None,
        )

    # Assert — no AI call occurs (NF.REQ1)
    assert result == {"ok": True, "message": "No update ID"}


@pytest.mark.asyncio
async def test_prompt_creates_job_and_publishes_to_queue(monkeypatch):
    # Arrange — verifies F.REQ3 (job created) and F.REQ4 (enqueued)
    db = _make_db()
    bt = _make_background_tasks()
    mock_mq = AsyncMock()
    mock_mq.publish_job = AsyncMock()

    with (
        patch("api.telegram.service.handler.handle_auth_step",
              new=AsyncMock(return_value=None)),
        patch("api.telegram.service.handler.get_user_session",
              new=AsyncMock(return_value=MagicMock(saas_key="key123"))),
        patch("api.telegram.service.handler.create_job",
              new=AsyncMock(return_value=JOB_ID)) as mock_create,
        patch("api.telegram.service.handler.get_mq",
              new=AsyncMock(return_value=mock_mq)),
        patch("api.telegram.service.handler.send_message", new=AsyncMock()),
    ):
        # Act
        result = await handle_telegram_update(
            db=db, raw_update={}, update=_make_update("/prompt build a café site"),
            background_tasks=bt,
            telegram_update_db_id=42,
        )

    # Assert
    assert result["ok"] is True
    assert result["job_id"] == str(JOB_ID)
    mock_create.assert_awaited_once()
    mock_mq.publish_job.assert_awaited_once_with(JOB_ID, attempt=1)
    db.commit.assert_awaited_once()