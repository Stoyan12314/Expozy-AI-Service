import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

import api.orchestrator.worker.service.worker_service as worker


@pytest.fixture(autouse=True)
def _patch_worker_settings(monkeypatch):
    """
    The worker module reads settings at import time.
    For unit tests, a simple object will be used .
    """
    monkeypatch.setattr(
        worker,
        "settings",
        SimpleNamespace(
            ai_provider="mock",
            max_retries=3,
            preview_base_url="https://play.myexpozy.com",
            retry_base_delay=1,
            retry_max_delay=60,
        ),
    )


@pytest.fixture(autouse=True)
def _fresh_shutdown_event(monkeypatch):
    # ensure each test has a fresh event so tests don't leak state
    monkeypatch.setattr(worker, "shutdown_event", asyncio.Event())


@pytest.mark.asyncio
async def test_process_job_happy_path_completes(monkeypatch):
    """
    Covers:
    - JobAttempt created
    - status QUEUED -> RUNNING -> COMPLETED
    - sanitizer + render + storage called
    - notify_job_completed called
    """
    job_id = UUID("00000000-0000-0000-0000-000000000111")
    bundle_id = UUID("00000000-0000-0000-0000-000000000222")

    job = SimpleNamespace(
        id=job_id,
        status=worker.JobStatus.QUEUED,
        prompt_text="build landing page",
        chat_id=123,
    )

    monkeypatch.setattr(worker, "fetch_job", AsyncMock(return_value=job))
    monkeypatch.setattr(worker, "update_job_status", AsyncMock())
    monkeypatch.setattr(worker, "create_job_attempt", AsyncMock(return_value=42))
    monkeypatch.setattr(worker, "finish_job_attempt", AsyncMock())

    # AI result success
    ai_result = SimpleNamespace(
        success=True,
        template={"metadata": {"name": "X"}, "sections": []},
        retryable=False,
        error=None,
        raw_response=None,
        validation=None,
    )
    monkeypatch.setattr(worker, "call_ai_provider", AsyncMock(return_value=ai_result))

    # sanitizer
    sanitizer = SimpleNamespace(sanitize_template=lambda t: t)
    monkeypatch.setattr(worker, "get_sanitizer", lambda: sanitizer)

    # render + storage
    monkeypatch.setattr(worker, "render_template_to_html", lambda tpl: "<html></html>")

    storage = SimpleNamespace(create_bundle=AsyncMock(return_value=bundle_id))
    monkeypatch.setattr(worker, "get_storage", lambda: storage)

    monkeypatch.setattr(worker, "notify_job_completed", AsyncMock())

    done = await worker.process_job(job_id=job_id, attempt=1)
    assert done is True

    # status updates
    worker.update_job_status.assert_any_await(job_id, worker.JobStatus.RUNNING, increment_attempts=True)
    worker.update_job_status.assert_any_await(
        job_id,
        worker.JobStatus.COMPLETED,
        bundle_id=bundle_id,
        preview_url=f"/p/{bundle_id}/index.html",
        raw_ai_response=ai_result.template,
    )

    # attempt lifecycle
    worker.create_job_attempt.assert_awaited_once_with(job_id, 1, "mock")
    worker.finish_job_attempt.assert_awaited()  # SUCCESS

    # notify telegram
    worker.notify_job_completed.assert_awaited_once()
    kwargs = worker.notify_job_completed.await_args.args
    assert kwargs[0] == 123  # chat_id


@pytest.mark.asyncio
async def test_process_job_retryable_failure_requeues(monkeypatch):
    """
    Covers:
    - status -> RUNNING then back to QUEUED
    - attempt outcome FAIL
    - returns False (meaning "retry")
    """
    job_id = UUID("00000000-0000-0000-0000-000000000333")

    job = SimpleNamespace(
        id=job_id,
        status=worker.JobStatus.QUEUED,
        prompt_text="x",
        chat_id=123,
    )

    monkeypatch.setattr(worker, "fetch_job", AsyncMock(return_value=job))
    monkeypatch.setattr(worker, "update_job_status", AsyncMock())
    monkeypatch.setattr(worker, "create_job_attempt", AsyncMock(return_value=7))
    monkeypatch.setattr(worker, "finish_job_attempt", AsyncMock())

    ai_result = SimpleNamespace(
        success=False,
        template=None,
        retryable=True,
        error="rate limit",
        raw_response={"raw": "x"},
        validation=None,
    )
    monkeypatch.setattr(worker, "call_ai_provider", AsyncMock(return_value=ai_result))

    done = await worker.process_job(job_id=job_id, attempt=1)
    assert done is False

    worker.update_job_status.assert_any_await(job_id, worker.JobStatus.RUNNING, increment_attempts=True)
    worker.update_job_status.assert_any_await(
        job_id,
        worker.JobStatus.QUEUED,
        error_message="rate limit",
        raw_ai_response={"error": "rate limit", "raw": ai_result.raw_response},
    )
    worker.finish_job_attempt.assert_awaited()  # FAIL


@pytest.mark.asyncio
async def test_process_job_permanent_failure_marks_failed(monkeypatch):
    """
    Covers:
    - status -> RUNNING then FAILED
    - failure reason stored
    - returns True (finished)
    """
    job_id = UUID("00000000-0000-0000-0000-000000000444")
    job = SimpleNamespace(
        id=job_id,
        status=worker.JobStatus.QUEUED,
        prompt_text="x",
        chat_id=123,
    )

    monkeypatch.setattr(worker, "fetch_job", AsyncMock(return_value=job))
    monkeypatch.setattr(worker, "update_job_status", AsyncMock())
    monkeypatch.setattr(worker, "create_job_attempt", AsyncMock(return_value=9))
    monkeypatch.setattr(worker, "finish_job_attempt", AsyncMock())

    ai_result = SimpleNamespace(
        success=False,
        template=None,
        retryable=False,
        error="bad request",
        raw_response={"raw": "x"},
        validation=None,
    )
    monkeypatch.setattr(worker, "call_ai_provider", AsyncMock(return_value=ai_result))

    done = await worker.process_job(job_id=job_id, attempt=1)
    assert done is True

    worker.update_job_status.assert_any_await(job_id, worker.JobStatus.RUNNING, increment_attempts=True)
    worker.update_job_status.assert_any_await(
        job_id,
        worker.JobStatus.FAILED,
        error_message="bad request",
        raw_ai_response={"error": "bad request", "raw": ai_result.raw_response},
        validation_errors=None,
    )


@pytest.mark.asyncio
async def test_process_job_exception_requeues_until_max_retries(monkeypatch):
    """
    Covers:
    - exceptions don't crash the worker
    - for attempt < max_retries: status set back to QUEUED and returns False
    """
    job_id = UUID("00000000-0000-0000-0000-000000000555")
    job = SimpleNamespace(
        id=job_id,
        status=worker.JobStatus.QUEUED,
        prompt_text="x",
        chat_id=123,
    )

    monkeypatch.setattr(worker, "fetch_job", AsyncMock(return_value=job))
    monkeypatch.setattr(worker, "update_job_status", AsyncMock())
    monkeypatch.setattr(worker, "create_job_attempt", AsyncMock(return_value=10))
    monkeypatch.setattr(worker, "finish_job_attempt", AsyncMock())

    async def boom(_prompt):
        raise RuntimeError("AI down")

    monkeypatch.setattr(worker, "call_ai_provider", AsyncMock(side_effect=boom))

    done = await worker.process_job(job_id=job_id, attempt=1)
    assert done is False

    worker.update_job_status.assert_any_await(job_id, worker.JobStatus.QUEUED, error_message="AI down")


@pytest.mark.asyncio
async def test_handle_message_schedules_retry_publish_delayed(monkeypatch):
    """
    Covers:
    - if process_job returns False and attempt < max_retries
      then publish_job_delayed is called with attempt+1
    """
    job_id = UUID("00000000-0000-0000-0000-000000000666")
    message = SimpleNamespace(job_id=job_id, attempt=1)

    monkeypatch.setattr(worker, "process_job", AsyncMock(return_value=False))

    fake_mq = SimpleNamespace(publish_job_delayed=AsyncMock())

    @asynccontextmanager
    async def fake_get_message_queue():
        yield fake_mq

    monkeypatch.setattr(worker, "get_message_queue", fake_get_message_queue)

    await worker.handle_message(message)

    fake_mq.publish_job_delayed.assert_awaited_once()
    args = fake_mq.publish_job_delayed.await_args.args
    assert args[0] == job_id
    assert args[1] == 2  # next attempt


@pytest.mark.asyncio
async def test_run_worker_starts_consuming_and_stops_on_shutdown(monkeypatch):
    """
    Covers:
    - Worker consumes RabbitMQ messages (mq.consume called)
    - Stops cleanly on shutdown_event (doesn't crash)
    """
    called = {}

    async def consume(handler):
        called["handler"] = handler
        try:
            await asyncio.Event().wait()  # block forever until cancelled
        except asyncio.CancelledError:
            called["cancelled"] = True
            raise

    fake_mq = SimpleNamespace(consume=consume)

    @asynccontextmanager
    async def fake_get_message_queue():
        yield fake_mq

    monkeypatch.setattr(worker, "get_message_queue", fake_get_message_queue)

    task = asyncio.create_task(worker.run_worker())
    await asyncio.sleep(0)  # let it start
    worker.shutdown_event.set()
    await task

    assert "handler" in called
    assert called["handler"] == worker.handle_message
