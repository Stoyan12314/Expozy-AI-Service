"""
Shared configuration using Pydantic Settings v2.
Environment variables are automatically loaded.
"""

from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field, PostgresDsn, AmqpDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # Database
    database_url: PostgresDsn = Field(
        default="postgresql+asyncpg://orchestrator:orchestrator_secret@localhost:5432/orchestrator"
    )
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=20, ge=0, le=100)
    
    # RabbitMQ
    rabbitmq_url: AmqpDsn = Field(
        default="amqp://orchestrator:orchestrator_secret@localhost:5672/"
    )
    job_queue_name: str = Field(default="ai_jobs")
    
    # Telegram
    telegram_secret_token: str = Field(default="your-telegram-secret-token")
    telegram_bot_token: str = Field(default="")  # For sending responses
    
    # AI Provider
    ai_provider: Literal["vertex", "gemini", "openai", "mock"] = Field(default="mock")
    ai_api_key: str = Field(default="")
    ai_model: str = Field(default="gemini-2.0-flash-001")
    ai_timeout: float = Field(default=120.0, ge=10.0, le=300.0)
    
    # Vertex AI (Google Cloud) settings
    vertex_project_id: Optional[str] = Field(default=None)
    vertex_region: str = Field(default="europe-west1")
    vertex_service_account_json: Optional[str] = Field(default=None)
    
    # Worker
    max_retries: int = Field(default=3, ge=1, le=10)
    retry_base_delay: float = Field(default=5.0, ge=1.0, le=60.0)
    retry_max_delay: float = Field(default=300.0, ge=60.0, le=600.0)
    
    # Preview Storage
    previews_path: str = Field(default="/previews")
    preview_base_url: str = Field(default="")

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    log_format: Literal["json", "console"] = Field(default="json")
    
    @property
    def database_url_sync(self) -> str:
        """Convert async URL to sync for Alembic."""
        return str(self.database_url).replace("+asyncpg", "")


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()