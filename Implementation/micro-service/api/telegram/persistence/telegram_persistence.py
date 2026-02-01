from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.orchestrator.db.models import Job, TelegramUpdate as TelegramUpdateDB


async def insert_telegram_update_dedup(
    db: AsyncSession,
    *,
    update_id: int,
    raw_update: dict[str, Any],
) -> Optional[int]:
    """
    Insert TelegramUpdate row with ON CONFLICT DO NOTHING.
    Returns telegram_update_db_id if inserted, otherwise None (duplicate).
    """
    stmt = (
        insert(TelegramUpdateDB)
        .values(update_id=update_id, raw_update=raw_update)
        .on_conflict_do_nothing(index_elements=["update_id"])
        .returning(TelegramUpdateDB.id)
    )
    result = await db.execute(stmt)
    row = result.fetchone()
    return row[0] if row else None


async def find_job_by_update_id(db: AsyncSession, update_id: int) -> Optional[Job]:
    q = (
        select(Job)
        .join(TelegramUpdateDB, Job.telegram_update_id == TelegramUpdateDB.id)
        .where(TelegramUpdateDB.update_id == update_id)
    )
    result = await db.execute(q)
    return result.scalar_one_or_none()


async def create_job(
    db: AsyncSession,
    *,
    telegram_update_id: int,
    chat_id: int,
    user_id: Optional[int],
    prompt_text: str,
) -> UUID:
    job = Job(
        telegram_update_id=telegram_update_id,
        chat_id=chat_id,
        user_id=user_id,
        prompt_text=prompt_text,
        status="queued",
    )
    db.add(job)
    await db.flush()  
    return job.id


async def mark_job_failed(
    db: AsyncSession,
    job: Job,
    *,
    error_message: str,
) -> None:
    job.status = "failed"
    job.error_message = error_message
    await db.flush()
