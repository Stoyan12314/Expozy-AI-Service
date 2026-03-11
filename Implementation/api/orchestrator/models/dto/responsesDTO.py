"""
DTOs for FastAPI responses.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class WebhookResponse(BaseModel):
    ok: bool = True
    job_id: Optional[UUID] = None
    message: Optional[str] = None


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    request_id: Optional[str] = None
