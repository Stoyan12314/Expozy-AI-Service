from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, AmqpDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
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

    # RabbitMQ
    rabbitmq_url: AmqpDsn = Field(
        default="amqp://orchestrator:orchestrator_secret@localhost:5672/"
    )
    job_queue_name: str = Field(default="ai_jobs")

    # Telegram
    telegram_bot_token: str
    telegram_secret_token: str

    # AI Provider (Vertex only)
    ai_provider: Literal["vertex"] = "vertex"
    ai_model: str = "gemini-2.5-pro"
    ai_timeout: float = Field(default=120.0, ge=10.0, le=300.0)

    # Vertex config
    vertex_project_id: str
    vertex_region: str = "europe-west1"
    vertex_service_account_json: str

    # Worker
    max_retries: int = Field(default=5, ge=1, le=10)
    retry_base_delay: float = Field(default=2.0, ge=1.0, le=60.0)
    retry_max_delay: float = Field(default=300.0, ge=60.0, le=600.0)

    # Preview
    previews_path: str = Field(default="/previews")
    preview_base_url: str = Field(default="http://localhost:8001")

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
