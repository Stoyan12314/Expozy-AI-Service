"""
Pytest fixtures for AI Orchestrator tests.

Provides:
- Async database session (in-memory SQLite or real PostgreSQL)
- FastAPI test client
- Mock RabbitMQ publisher
- Mock Telegram client
- Sample data factories
"""

import asyncio
import os
from datetime import datetime, timezone
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from api.orchestrator.db.models import Base, Job, JobStatus, TelegramUpdate, JobAttempt
from shared.config import Settings


# =============================================================================
# EVENT LOOP FIXTURE
# =============================================================================

@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# DATABASE FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def async_engine():
    """Create async SQLite engine for testing."""
    # Use SQLite for fast unit tests
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    
    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield engine
    
    # Cleanup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create async database session for testing."""
    async_session_maker = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    
    async with async_session_maker() as session:
        yield session
        await session.rollback()


@pytest.fixture
def db_session_factory(async_engine):
    """Create session factory for dependency injection."""
    return async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


# =============================================================================
# MOCK FIXTURES
# =============================================================================

@pytest.fixture
def mock_mq():
    """Mock RabbitMQ message queue."""
    mq = AsyncMock()
    mq.publish_job = AsyncMock(return_value=None)
    mq.publish_job_delayed = AsyncMock(return_value=None)
    mq.connect = AsyncMock()
    mq.close = AsyncMock()
    return mq


@pytest.fixture
def mock_telegram():
    """Mock Telegram client."""
    client = AsyncMock()
    client.send_message = AsyncMock(return_value={"message_id": 123})
    client.send_message_fire_and_forget = AsyncMock()
    client.is_configured = True
    return client


@pytest.fixture
def mock_ai_provider():
    """Mock AI provider that returns valid template."""
    from api.orchestrator.ai.providers.ai_provider import GenerationResult
    
    provider = AsyncMock()
    provider.generate = AsyncMock(return_value=GenerationResult(
        success=True,
        template={
            "metadata": {"name": "Test Template"},
            "theme": {"primaryColor": "#3B82F6"},
            "sections": [
                {
                    "type": "hero",
                    "title": "Test Page",
                    "subtitle": "Generated for testing",
                }
            ]
        },
        error=None,
        retryable=False,
        raw_response=None,
        validation=None,
    ))
    return provider


# =============================================================================
# SETTINGS FIXTURE
# =============================================================================

@pytest.fixture
def test_settings():
    """Test settings with mock values."""
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        telegram_bot_token="test-bot-token",
        telegram_secret_token="test-secret-token",
        ai_provider="mock",
        ai_api_key="test-key",
        previews_path="/tmp/test-previews",
        max_retries=3,
    )


# =============================================================================
# SAMPLE DATA FACTORIES
# =============================================================================

@pytest.fixture
def sample_telegram_update():
    """Factory for creating sample Telegram updates."""
    def _create(update_id: int = None, chat_id: int = 12345, text: str = "Test prompt"):
        return {
            "update_id": update_id or int(datetime.now().timestamp() * 1000),
            "message": {
                "message_id": 1,
                "date": int(datetime.now().timestamp()),
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": chat_id, "is_bot": False, "first_name": "Test"},
                "text": text,
            }
        }
    return _create


@pytest.fixture
def sample_job_data():
    """Factory for creating sample job data."""
    def _create(
        chat_id: int = 12345,
        prompt: str = "Create a landing page",
        status: JobStatus = JobStatus.QUEUED,
    ):
        return {
            "chat_id": chat_id,
            "user_id": chat_id,
            "prompt_text": prompt,
            "status": status,
        }
    return _create


@pytest_asyncio.fixture
async def sample_job(db_session, sample_job_data) -> Job:
    """Create a sample job in the database."""
    job = Job(**sample_job_data())
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


@pytest_asyncio.fixture
async def sample_telegram_update_db(db_session, sample_telegram_update) -> TelegramUpdate:
    """Create a sample telegram update in the database."""
    update_data = sample_telegram_update()
    telegram_update = TelegramUpdate(
        update_id=update_data["update_id"],
        raw_update=update_data,
    )
    db_session.add(telegram_update)
    await db_session.commit()
    await db_session.refresh(telegram_update)
    return telegram_update


# =============================================================================
# TEMP DIRECTORY FIXTURE
# =============================================================================

@pytest.fixture
def temp_previews_dir(tmp_path):
    """Create temporary previews directory."""
    previews_dir = tmp_path / "previews"
    previews_dir.mkdir()
    return previews_dir
