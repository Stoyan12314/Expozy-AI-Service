from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from api.orchestrator.db.models import Job, JobAttempt, JobStatus, AttemptOutcome
from api.orchestrator.db.session import get_db_session


async def fetch_job(job_id: UUID) -> Optional[Job]:
    async with get_db_session() as db:
        result = await db.execute(
            select(Job)
            .options(selectinload(Job.attempts))
            .where(Job.id == job_id)
        )
        return result.scalar_one_or_none()


async def update_job_status(
    job_id: UUID,
    status: JobStatus,
    *,
    bundle_id: Optional[UUID] = None,
    preview_url: Optional[str] = None,
    error_message: Optional[str] = None,
    raw_ai_response: Optional[dict] = None,
    validation_errors: Optional[dict] = None,
    increment_attempts: bool = False,
) -> None:
    async with get_db_session() as db:
        values = {
            "status": status,
            "updated_at": datetime.now(timezone.utc),
        }

        if bundle_id is not None:
            values["bundle_id"] = bundle_id
        if preview_url is not None:
            values["preview_url"] = preview_url
        if error_message is not None:
            values["error_message"] = error_message
        if raw_ai_response is not None:
            values["raw_ai_response"] = raw_ai_response
        if validation_errors is not None:
            values["validation_errors"] = validation_errors

        if increment_attempts:
            stmt = update(Job).where(Job.id == job_id).values(
                **values,
                attempt_count=Job.attempt_count + 1,
            )
        else:
            stmt = update(Job).where(Job.id == job_id).values(**values)

        await db.execute(stmt)
        await db.commit()


async def create_job_attempt(job_id: UUID, attempt_no: int, provider_name: str) -> int:
    async with get_db_session() as db:
        attempt = JobAttempt(
            job_id=job_id,
            attempt_no=attempt_no,
            provider_name=provider_name,
        )
        db.add(attempt)
        await db.flush()
        attempt_id = attempt.id
        await db.commit()
        return attempt_id


async def finish_job_attempt(
    attempt_id: int,
    outcome: AttemptOutcome,
    *,
    error_detail: Optional[str] = None,
    provider_status_code: Optional[int] = None,
) -> None:
    async with get_db_session() as db:
        result = await db.execute(select(JobAttempt).where(JobAttempt.id == attempt_id))
        attempt = result.scalar_one_or_none()

        if not attempt:
            return

        attempt.finished_at = datetime.now(timezone.utc)
        attempt.outcome = outcome
        attempt.error_detail = error_detail
        attempt.provider_status_code = provider_status_code

        if attempt.started_at and attempt.finished_at:
            delta = attempt.finished_at - attempt.started_at
            attempt.duration_ms = int(delta.total_seconds() * 1000)

        await db.commit()
