import pytest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import api.telegram.service.telegram_service as svc

@dataclass
class FakeUpdate:
    update_id: int
    text: str | None
    chat_id: int | None
    user_id: int | None

    def get_text(self):
        return self.text

    def get_chat_id(self):
        return self.chat_id

    def get_user_id(self):
        return self.user_id


@pytest.mark.asyncio
async def test_ignore_when_no_text_or_chat_id():
    """Behaviour: ignore updates that have no text OR no chat_id."""
    db = AsyncMock()

    decision = await svc.handle_telegram_update(
        db=db,
        raw_update={"x": 1},
        update=FakeUpdate(update_id=1, text=None, chat_id=123, user_id=1),
    )
    assert decision.message == "Ignored"
    assert decision.job_id is None

    decision2 = await svc.handle_telegram_update(
        db=db,
        raw_update={"x": 1},
        update=FakeUpdate(update_id=2, text="/prompt hi", chat_id=None, user_id=1),
    )
    assert decision2.message == "Ignored"
    assert decision2.job_id is None


@pytest.mark.asyncio
async def test_start_command_returns_welcome_message_key():
    """Behaviour: /start returns send_text=WELCOME_MESSAGE and no side effects."""
    db = AsyncMock()

    decision = await svc.handle_telegram_update(
        db=db,
        raw_update={},
        update=FakeUpdate(update_id=1, text="/start", chat_id=123, user_id=1),
    )

    assert decision.send_text == "WELCOME_MESSAGE"
    assert decision.message == "Start"
    assert decision.job_id is None
    assert decision.notify_started is False


@pytest.mark.asyncio
async def test_help_command_returns_help_message_key():
    """Behaviour: /help returns send_text=HELP_MESSAGE."""
    db = AsyncMock()

    decision = await svc.handle_telegram_update(
        db=db,
        raw_update={},
        update=FakeUpdate(update_id=1, text="/help", chat_id=123, user_id=1),
    )

    assert decision.send_text == "HELP_MESSAGE"
    assert decision.message == "Help"
    assert decision.job_id is None


@pytest.mark.asyncio
async def test_invalid_command_returns_invalid_command_message_key():
    """Behaviour: any non-/prompt text returns INVALID_COMMAND_MESSAGE."""
    db = AsyncMock()

    decision = await svc.handle_telegram_update(
        db=db,
        raw_update={},
        update=FakeUpdate(update_id=1, text="hello", chat_id=123, user_id=1),
    )

    assert decision.send_text == "INVALID_COMMAND_MESSAGE"
    assert decision.message == "Invalid command"
    assert decision.job_id is None


@pytest.mark.asyncio
async def test_empty_prompt_returns_empty_prompt_message_key():
    """Behaviour: /prompt with no text returns EMPTY_PROMPT_MESSAGE."""
    db = AsyncMock()

    decision = await svc.handle_telegram_update(
        db=db,
        raw_update={},
        update=FakeUpdate(update_id=1, text="/prompt   ", chat_id=123, user_id=1),
    )

    assert decision.send_text == "EMPTY_PROMPT_MESSAGE"
    assert decision.message == "Empty prompt"
    assert decision.job_id is None


@pytest.mark.asyncio
async def test_valid_prompt_creates_job_commits_and_publishes_after_commit(monkeypatch):
    """Behaviour: valid /prompt -> dedupe -> create job -> commit -> publish -> notify_started."""
    db = AsyncMock()
    committed = {"done": False}

    async def commit_side_effect():
        committed["done"] = True

    db.commit.side_effect = commit_side_effect

    monkeypatch.setattr(svc, "insert_telegram_update_dedup", AsyncMock(return_value=99))
    monkeypatch.setattr(svc, "create_job", AsyncMock(return_value="job-uuid-123"))
    monkeypatch.setattr(svc, "find_job_by_update_id", AsyncMock())
    monkeypatch.setattr(svc, "mark_job_failed", AsyncMock())

    mq = SimpleNamespace()

    async def publish_job(job_id, attempt=1):
        assert committed["done"] is True, "publish_job called before db.commit()"
        assert attempt == 1

    mq.publish_job = AsyncMock(side_effect=publish_job)
    monkeypatch.setattr(svc, "get_mq", AsyncMock(return_value=mq))

    decision = await svc.handle_telegram_update(
        db=db,
        raw_update={"update_id": 1},
        update=FakeUpdate(update_id=1, text="/prompt build a landing page", chat_id=555, user_id=777),
    )

    assert decision.job_id == "job-uuid-123"
    assert decision.notify_started is True
    assert decision.ok is True

    svc.insert_telegram_update_dedup.assert_awaited_once()
    svc.create_job.assert_awaited_once()
    db.commit.assert_awaited_once()
    mq.publish_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_update_returns_existing_job_and_does_not_publish(monkeypatch):
    """Behaviour: duplicate update -> return existing job_id and do not create/publish."""
    db = AsyncMock()

    monkeypatch.setattr(svc, "insert_telegram_update_dedup", AsyncMock(return_value=None))

    existing_job = SimpleNamespace(id="job-uuid-existing")
    monkeypatch.setattr(svc, "find_job_by_update_id", AsyncMock(return_value=existing_job))

    monkeypatch.setattr(svc, "create_job", AsyncMock())
    monkeypatch.setattr(svc, "get_mq", AsyncMock())

    decision = await svc.handle_telegram_update(
        db=db,
        raw_update={"update_id": 1},
        update=FakeUpdate(update_id=1, text="/prompt x", chat_id=555, user_id=777),
    )

    assert decision.job_id == "job-uuid-existing"
    assert decision.message == "Already processing"

    svc.create_job.assert_not_awaited()
    svc.get_mq.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_queue_publish_failure_marks_job_failed_and_returns_warning(monkeypatch):
    """Behaviour: if publish fails -> mark job failed + return warning send_text."""
    db = AsyncMock()

    monkeypatch.setattr(svc, "insert_telegram_update_dedup", AsyncMock(return_value=99))
    monkeypatch.setattr(svc, "create_job", AsyncMock(return_value="job-uuid-123"))

    job_obj = SimpleNamespace(id="job-uuid-123")
    monkeypatch.setattr(svc, "find_job_by_update_id", AsyncMock(return_value=job_obj))
    monkeypatch.setattr(svc, "mark_job_failed", AsyncMock())

    mq = SimpleNamespace()
    mq.publish_job = AsyncMock(side_effect=RuntimeError("rabbit down"))
    monkeypatch.setattr(svc, "get_mq", AsyncMock(return_value=mq))

    decision = await svc.handle_telegram_update(
        db=db,
        raw_update={"update_id": 1},
        update=FakeUpdate(update_id=1, text="/prompt x", chat_id=555, user_id=777),
    )

    assert decision.message == "Queue publish failed"
    assert decision.send_text is not None
    assert decision.job_id == "job-uuid-123"

    svc.mark_job_failed.assert_awaited_once()
