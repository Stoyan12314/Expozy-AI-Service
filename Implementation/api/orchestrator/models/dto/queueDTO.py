"""
DTO for RabbitMQ payloads between API and worker.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class JobQueueMessage(BaseModel):
    job_id: UUID
    attempt: int = 1

    model_config = ConfigDict(frozen=True)
