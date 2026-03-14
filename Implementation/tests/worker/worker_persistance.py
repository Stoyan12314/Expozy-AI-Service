"""
Tests for worker persistence functions — fetch_job, update_job_status,
create_job_attempt, finish_job_attempt.

The database session is patched via AsyncMock — no real PostgreSQL instance
is required in CI/CD (NF.REQ6). Tests verify that the correct SQL operations
are executed with the correct arguments (F.REQ3, F.REQ13, NF.REQ4).

Each test is divided into Arrange, Act, and Assert.
"""

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from api.orchestrator.db.models import JobStatus, AttemptOutcome


class TestWorkerPersistance:

    def _make_mock_session(self):
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        return session

    # ── update_job_status ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_update_job_status_commits(self):
        """
        update_job_status() must commit after writing so status changes
        are durable (F.REQ13 — QUEUED/RUNNING/SUCCEEDED/FAILED persisted).
        """
        # Arrange
        from api.orchestrator.worker.persistance.worker_persistance import update_job_status
        mock_db = self._make_mock_session()
        mock_db.execute = AsyncMock()

        # Act
        with patch("api.orchestrator.worker.persistance.worker_persistance.get_db_session", return_value=mock_db):
            await update_job_status(uuid.uuid4(), JobStatus.RUNNING)

        # Assert
        mock_db.commit.assert_called_once()

    # ── fetch_job ─────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_fetch_job_returns_none_when_not_found(self):
        """
        fetch_job() must return None when the job row is absent rather than
        raising — callers check for None before proceeding (F.REQ13).
        """
        # Arrange
        from api.orchestrator.worker.persistance.worker_persistance import fetch_job
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)

        mock_db = self._make_mock_session()
        mock_db.execute = AsyncMock(return_value=mock_result)

        # Act
        with patch("api.orchestrator.worker.persistance.worker_persistance.get_db_session", return_value=mock_db):
            result = await fetch_job(uuid.uuid4())

        # Assert
        assert result is None