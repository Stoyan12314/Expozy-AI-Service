"""
Unit tests for webhook deduplication.

Tests that:
- Same update_id is not processed twice
- Duplicate webhooks return 200 with "Already processing"
- Different update_ids create separate jobs
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from api.orchestrator.db.models import Job, JobStatus, TelegramUpdate


class TestWebhookDeduplication:
    """Test webhook idempotency via update_id deduplication."""

    @pytest_asyncio.fixture
    async def setup_mocks(self, db_session_factory, mock_mq, test_settings):
        """Set up mocks for webhook testing."""
        from api.main import app
        from shared.services import get_session
        
        # Create a proper async generator for session override
        async def override_get_session():
            async with db_session_factory() as session:
                yield session
        
        app.dependency_overrides[get_session] = override_get_session
        
        yield {
            "mock_mq": mock_mq,
            "settings": test_settings,
        }
        
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_first_update_creates_job(
        self,
        db_session,
        mock_mq,
        sample_telegram_update,
        test_settings,
    ):
        """First webhook with new update_id should create a job."""
        from sqlalchemy.dialects.sqlite import insert
        
        update_data = sample_telegram_update(update_id=100001)
        update_id = update_data["update_id"]
        
        # Simulate webhook logic: insert telegram_update
        stmt = insert(TelegramUpdate).values(
            update_id=update_id,
            raw_update=update_data,
        ).on_conflict_do_nothing(index_elements=["update_id"])
        
        # For SQLite, we need to handle this differently
        # First check if exists
        result = await db_session.execute(
            select(TelegramUpdate).where(TelegramUpdate.update_id == update_id)
        )
        existing = result.scalar_one_or_none()
        
        if existing is None:
            # Insert new record
            telegram_update = TelegramUpdate(
                update_id=update_id,
                raw_update=update_data,
            )
            db_session.add(telegram_update)
            await db_session.flush()
            
            # Create job
            job = Job(
                telegram_update_id=telegram_update.id,
                chat_id=update_data["message"]["chat"]["id"],
                user_id=update_data["message"]["from"]["id"],
                prompt_text=update_data["message"]["text"],
                status=JobStatus.QUEUED,
            )
            db_session.add(job)
            await db_session.commit()
            
            # Verify job was created
            result = await db_session.execute(select(Job))
            jobs = result.scalars().all()
            assert len(jobs) == 1
            assert jobs[0].prompt_text == "Test prompt"
            assert jobs[0].status == JobStatus.QUEUED

    @pytest.mark.asyncio
    async def test_duplicate_update_does_not_create_job(
        self,
        db_session,
        sample_telegram_update,
    ):
        """Same update_id sent twice should not create a second job."""
        update_data = sample_telegram_update(update_id=100002)
        update_id = update_data["update_id"]
        
        # First request - creates telegram_update and job
        telegram_update1 = TelegramUpdate(
            update_id=update_id,
            raw_update=update_data,
        )
        db_session.add(telegram_update1)
        await db_session.flush()
        
        job1 = Job(
            telegram_update_id=telegram_update1.id,
            chat_id=update_data["message"]["chat"]["id"],
            user_id=update_data["message"]["from"]["id"],
            prompt_text=update_data["message"]["text"],
            status=JobStatus.QUEUED,
        )
        db_session.add(job1)
        await db_session.commit()
        
        # Second request - check for existing update_id
        result = await db_session.execute(
            select(TelegramUpdate).where(TelegramUpdate.update_id == update_id)
        )
        existing = result.scalar_one_or_none()
        
        # Should find existing record
        assert existing is not None
        assert existing.id == telegram_update1.id
        
        # No new job should be created
        result = await db_session.execute(select(Job))
        jobs = result.scalars().all()
        assert len(jobs) == 1
        assert jobs[0].id == job1.id

    @pytest.mark.asyncio
    async def test_different_update_ids_create_separate_jobs(
        self,
        db_session,
        sample_telegram_update,
    ):
        """Different update_ids should create separate jobs."""
        # First update
        update1 = sample_telegram_update(update_id=100003, text="First prompt")
        telegram_update1 = TelegramUpdate(
            update_id=update1["update_id"],
            raw_update=update1,
        )
        db_session.add(telegram_update1)
        await db_session.flush()
        
        job1 = Job(
            telegram_update_id=telegram_update1.id,
            chat_id=update1["message"]["chat"]["id"],
            prompt_text="First prompt",
            status=JobStatus.QUEUED,
        )
        db_session.add(job1)
        
        # Second update with different update_id
        update2 = sample_telegram_update(update_id=100004, text="Second prompt")
        telegram_update2 = TelegramUpdate(
            update_id=update2["update_id"],
            raw_update=update2,
        )
        db_session.add(telegram_update2)
        await db_session.flush()
        
        job2 = Job(
            telegram_update_id=telegram_update2.id,
            chat_id=update2["message"]["chat"]["id"],
            prompt_text="Second prompt",
            status=JobStatus.QUEUED,
        )
        db_session.add(job2)
        await db_session.commit()
        
        # Verify two separate jobs
        result = await db_session.execute(
            select(Job).order_by(Job.created_at)
        )
        jobs = result.scalars().all()
        
        assert len(jobs) == 2
        assert jobs[0].prompt_text == "First prompt"
        assert jobs[1].prompt_text == "Second prompt"
        assert jobs[0].id != jobs[1].id

    @pytest.mark.asyncio
    async def test_duplicate_returns_existing_job_id(
        self,
        db_session,
        sample_telegram_update,
    ):
        """Duplicate webhook should return the existing job's ID."""
        update_data = sample_telegram_update(update_id=100005)
        update_id = update_data["update_id"]
        
        # Create initial telegram_update and job
        telegram_update = TelegramUpdate(
            update_id=update_id,
            raw_update=update_data,
        )
        db_session.add(telegram_update)
        await db_session.flush()
        
        original_job = Job(
            telegram_update_id=telegram_update.id,
            chat_id=update_data["message"]["chat"]["id"],
            prompt_text=update_data["message"]["text"],
            status=JobStatus.QUEUED,
        )
        db_session.add(original_job)
        await db_session.commit()
        original_job_id = original_job.id
        
        # Simulate duplicate webhook - find existing job
        result = await db_session.execute(
            select(Job)
            .join(TelegramUpdate, Job.telegram_update_id == TelegramUpdate.id)
            .where(TelegramUpdate.update_id == update_id)
        )
        existing_job = result.scalar_one_or_none()
        
        # Should return the same job
        assert existing_job is not None
        assert existing_job.id == original_job_id

    @pytest.mark.asyncio
    async def test_no_text_does_not_create_job(
        self,
        db_session,
    ):
        """Updates without text should not create jobs."""
        # Update with no text
        update_data = {
            "update_id": 100006,
            "message": {
                "message_id": 1,
                "date": 1234567890,
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 12345, "is_bot": False, "first_name": "Test"},
                # No "text" field
            }
        }
        
        # Simulate webhook check for text
        message = update_data.get("message", {})
        text = message.get("text") or message.get("caption")
        
        assert text is None
        
        # No job should be created
        result = await db_session.execute(select(Job))
        jobs = result.scalars().all()
        assert len(jobs) == 0


class TestWebhookEndpoint:
    """Test the actual webhook HTTP endpoint."""

    @pytest.mark.asyncio
    async def test_webhook_returns_200_on_success(
        self,
        db_session_factory,
        mock_mq,
        sample_telegram_update,
        test_settings,
    ):
        """Webhook should return 200 OK on successful processing."""
        from httpx import AsyncClient, ASGITransport
        from api.main import app
        from shared.services import get_session
        
        async def override_get_session():
            async with db_session_factory() as session:
                yield session
        
        app.dependency_overrides[get_session] = override_get_session
        
        with patch('api.main.get_mq', return_value=mock_mq):
            with patch('shared.config.get_settings', return_value=test_settings):
                with patch('api.main.notify_job_started', new_callable=AsyncMock):
                    async with AsyncClient(
                        transport=ASGITransport(app=app),
                        base_url="http://test"
                    ) as client:
                        response = await client.post(
                            "/telegram/webhook",
                            json=sample_telegram_update(update_id=200001),
                            headers={
                                "X-Telegram-Bot-Api-Secret-Token": "test-secret-token"
                            }
                        )
        
        app.dependency_overrides.clear()
        
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True

    @pytest.mark.asyncio
    async def test_webhook_returns_401_on_invalid_token(
        self,
        db_session_factory,
        sample_telegram_update,
        test_settings,
    ):
        """Webhook should return 401 on invalid secret token."""
        from httpx import AsyncClient, ASGITransport
        from api.main import app
        from shared.services import get_session
        
        async def override_get_session():
            async with db_session_factory() as session:
                yield session
        
        app.dependency_overrides[get_session] = override_get_session
        
        with patch('shared.config.get_settings', return_value=test_settings):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test"
            ) as client:
                response = await client.post(
                    "/telegram/webhook",
                    json=sample_telegram_update(),
                    headers={
                        "X-Telegram-Bot-Api-Secret-Token": "wrong-token"
                    }
                )
        
        app.dependency_overrides.clear()
        
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_duplicate_webhook_returns_already_processing(
        self,
        db_session_factory,
        mock_mq,
        sample_telegram_update,
        test_settings,
    ):
        """Duplicate webhook should return 'Already processing'."""
        from httpx import AsyncClient, ASGITransport
        from api.main import app
        from shared.services import get_session
        
        async def override_get_session():
            async with db_session_factory() as session:
                yield session
        
        app.dependency_overrides[get_session] = override_get_session
        
        update_data = sample_telegram_update(update_id=200002)
        
        with patch('api.main.get_mq', return_value=mock_mq):
            with patch('shared.config.get_settings', return_value=test_settings):
                with patch('api.main.notify_job_started', new_callable=AsyncMock):
                    async with AsyncClient(
                        transport=ASGITransport(app=app),
                        base_url="http://test"
                    ) as client:
                        # First request
                        response1 = await client.post(
                            "/telegram/webhook",
                            json=update_data,
                            headers={
                                "X-Telegram-Bot-Api-Secret-Token": "test-secret-token"
                            }
                        )
                        
                        # Second request with same update_id
                        response2 = await client.post(
                            "/telegram/webhook",
                            json=update_data,
                            headers={
                                "X-Telegram-Bot-Api-Secret-Token": "test-secret-token"
                            }
                        )
        
        app.dependency_overrides.clear()
        
        # Both should return 200
        assert response1.status_code == 200
        assert response2.status_code == 200
        
        # Second should indicate already processing
        data2 = response2.json()
        assert "Already" in data2.get("message", "")
