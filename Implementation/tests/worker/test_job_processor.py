"""
Tests for JobProcessor.

The site generator and all database functions are patched so only the
processor's own logic is tested.
"""

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from api.orchestrator.db.models import JobStatus
from api.orchestrator.worker.service.job_processor import JobProcessor


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_job(status=JobStatus.QUEUED):
    job = MagicMock()
    job.id = uuid.uuid4()
    job.status = status
    job.prompt_text = "A café in Sofia"
    job.chat_id = 12345
    job.user_id = 99
    return job


def make_processor(site_result: dict):
    processor = JobProcessor.__new__(JobProcessor)
    processor.site_generator = MagicMock()
    processor.site_generator.generate = AsyncMock(return_value=site_result)
    return processor


def base_patches(job):
    return {
        "fetch_job": AsyncMock(return_value=job),
        "update_job_status": AsyncMock(),
        "create_job_attempt": AsyncMock(return_value=1),
        "finish_job_attempt": AsyncMock(),
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skips_already_completed_job():
    # Arrange
    job = make_job(status=JobStatus.COMPLETED)
    processor = make_processor({"success": True, "pages": {}})

    # Act
    with patch.multiple("api.orchestrator.worker.service.job_processor", **base_patches(job)):
        result = await processor.process(job.id, attempt=1)

    # Assert
    assert result is True
    processor.site_generator.generate.assert_not_called()


@pytest.mark.asyncio
async def test_returns_false_on_retryable_failure():
    # Arrange
    job = make_job()
    site_result = {
        "success": False, "pages": {},
        "errors": ["STR-001 missing tag"], "retryable": True,
        "total_latency_ms": 100, "business_context": None, "selected_pages": [],
    }
    processor = make_processor(site_result)

    # Act
    with (
        patch.multiple("api.orchestrator.worker.service.job_processor", **base_patches(job)),
        patch("api.orchestrator.worker.service.job_processor.get_settings") as mock_settings,
    ):
        mock_settings.return_value.max_retries = 3
        mock_settings.return_value.ai_provider = "stub"
        result = await processor.process(job.id, attempt=1)

    # Assert
    assert result is False


@pytest.mark.asyncio
async def test_returns_true_when_job_not_found():
    # Arrange
    processor = JobProcessor.__new__(JobProcessor)
    processor.site_generator = MagicMock()

    # Act
    with patch("api.orchestrator.worker.service.job_processor.fetch_job", new=AsyncMock(return_value=None)):
        result = await processor.process(uuid.uuid4(), attempt=1)

    # Assert
    assert result is True
    processor.site_generator.generate.assert_not_called()