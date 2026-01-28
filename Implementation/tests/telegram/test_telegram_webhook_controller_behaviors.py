import pytest
from dataclasses import dataclass
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

import api.telegram.controller.telegram_webhook as ctrl
from api.telegram.service.telegram_service import WebhookDecision
from shared.services import get_session


@dataclass
class FakeUpdate:
    chat_id: int = 123

    def get_chat_id(self):
        return self.chat_id


@pytest.fixture
def app(monkeypatch):
    # Controller reads settings at import time, but we can overwrite the value
    ctrl.settings.telegram_secret_token = "test-secret"

    # Ensure predictable mapping
    ctrl._MESSAGE_MAP["WELCOME_MESSAGE"] = "WELCOME!"
    ctrl._MESSAGE_MAP["HELP_MESSAGE"] = "HELP!"
    ctrl._MESSAGE_MAP["INVALID_COMMAND_MESSAGE"] = "INVALID!"
    ctrl._MESSAGE_MAP["EMPTY_PROMPT_MESSAGE"] = "EMPTY!"

    app = FastAPI()
    app.include_router(ctrl.router, prefix="/telegram")

    fake_db = object()
    app.dependency_overrides[get_session] = lambda: fake_db
    return app


@pytest.mark.asyncio
async def test_rejects_invalid_secret_token(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            json={"any": "thing"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_rejects_invalid_json_body(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/telegram/webhook",
            headers={
                "X-Telegram-Bot-Api-Secret-Token": "test-secret",
                "content-type": "application/json",
            },
            content=b"not-json",
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_rejects_invalid_telegram_update_format(app, monkeypatch):
    monkeypatch.setattr(
        ctrl.TelegramUpdate,
        "model_validate",
        classmethod(lambda cls, raw: (_ for _ in ()).throw(ValueError("bad"))),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json={"x": 1},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_send_text_schedules_send_telegram_message(app, monkeypatch):
    monkeypatch.setattr(
        ctrl.TelegramUpdate,
        "model_validate",
        classmethod(lambda cls, raw: FakeUpdate(chat_id=123)),
    )
    monkeypatch.setattr(
        ctrl,
        "handle_telegram_update",
        AsyncMock(return_value=WebhookDecision(ok=True, send_text="WELCOME_MESSAGE")),
    )

    send_mock = AsyncMock()
    monkeypatch.setattr(ctrl, "send_telegram_message", send_mock)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json={"update_id": 1},
        )

    assert r.status_code == 200
    send_mock.assert_awaited_once()
    assert send_mock.await_args.kwargs["chat_id"] == 123
    assert send_mock.await_args.kwargs["text"] == "WELCOME!"


@pytest.mark.asyncio
async def test_notify_started_schedules_notify_job_started(app, monkeypatch):
    job_id = UUID("00000000-0000-0000-0000-000000000123")

    monkeypatch.setattr(
        ctrl.TelegramUpdate,
        "model_validate",
        classmethod(lambda cls, raw: FakeUpdate(chat_id=999)),
    )
    monkeypatch.setattr(
        ctrl,
        "handle_telegram_update",
        AsyncMock(
            return_value=WebhookDecision(
                ok=True,
                job_id=job_id,
                notify_started=True,
            )
        ),
    )

    notify_mock = AsyncMock()
    monkeypatch.setattr(ctrl, "notify_job_started", notify_mock)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
            json={"update_id": 1},
        )

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["job_id"] == str(job_id)

    notify_mock.assert_awaited_once()
    assert notify_mock.await_args.kwargs["chat_id"] == 999
    assert notify_mock.await_args.kwargs["job_id"] == str(job_id)
