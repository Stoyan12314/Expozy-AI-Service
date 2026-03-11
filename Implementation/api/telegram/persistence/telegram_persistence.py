from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, update, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.orchestrator.db.models import Job, TelegramUpdate as TelegramUpdateDB
from api.orchestrator.db.models.telegram_session import TelegramSession, TelegramLoginState


# ── Job helpers ───────────────────────────────────────────────────────────────

async def insert_telegram_update_dedup(
    db: AsyncSession, *, update_id: int, raw_update: dict[str, Any],
) -> Optional[int]:
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
    db: AsyncSession, *, telegram_update_id: int, chat_id: int,
    user_id: Optional[int], prompt_text: str,
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


async def mark_job_failed(db: AsyncSession, job: Job, *, error_message: str) -> None:
    job.status = "failed"
    job.error_message = error_message
    await db.flush()


# ── Session (multi-store) ─────────────────────────────────────────────────────

async def save_user_session(
    db: AsyncSession,
    telegram_id: int,
    project: str,
    token: str,
    saas_key: str,
    project_url: str,
) -> None:
    """
    Upsert a session for (telegram_id, project).
    Marks this store as active and deactivates all others for this user.
    """
    # Deactivate all existing sessions for this user
    await db.execute(
        update(TelegramSession)
        .where(TelegramSession.telegram_id == telegram_id)
        .values(is_active=False)
    )

    # Upsert this store's session and make it active
    stmt = (
        insert(TelegramSession)
        .values(
            telegram_id=telegram_id,
            project=project,
            token=token,
            saas_key=saas_key,
            project_url=project_url,
            is_active=True,
        )
        .on_conflict_do_update(
            constraint="uq_telegram_sessions_user_project",
            set_={
                "token":       token,
                "saas_key":    saas_key,
                "project_url": project_url,
                "is_active":   True,
            },
        )
    )
    await db.execute(stmt)
    await db.commit()


async def get_user_session(db: AsyncSession, telegram_id: int) -> TelegramSession | None:
    """Returns the currently active session for the user."""
    result = await db.execute(
        select(TelegramSession).where(
            TelegramSession.telegram_id == telegram_id,
            TelegramSession.is_active == True,
        )
    )
    return result.scalar_one_or_none()


async def get_all_sessions(db: AsyncSession, telegram_id: int) -> list[TelegramSession]:
    """Returns all stores this user has connected via the bot."""
    result = await db.execute(
        select(TelegramSession)
        .where(TelegramSession.telegram_id == telegram_id)
        .order_by(TelegramSession.updated_at.desc())
    )
    return list(result.scalars().all())


async def get_session_by_project(
    db: AsyncSession, telegram_id: int, project: str
) -> TelegramSession | None:
    result = await db.execute(
        select(TelegramSession).where(
            TelegramSession.telegram_id == telegram_id,
            TelegramSession.project == project,
        )
    )
    return result.scalar_one_or_none()


async def set_active_session(db: AsyncSession, telegram_id: int, project: str) -> bool:
    """Switch the active store to `project`. Returns True if found and switched."""
    await db.execute(
        update(TelegramSession)
        .where(TelegramSession.telegram_id == telegram_id)
        .values(is_active=False)
    )
    result = await db.execute(
        update(TelegramSession)
        .where(
            TelegramSession.telegram_id == telegram_id,
            TelegramSession.project == project,
        )
        .values(is_active=True)
        .returning(TelegramSession.id)
    )
    await db.commit()
    return result.fetchone() is not None


async def delete_active_session(db: AsyncSession, telegram_id: int) -> None:
    """
    Soft-deletes the active session — clears the token and marks it inactive
    but preserves the row so saas_key survives for future /login calls.
    """
    await db.execute(
        update(TelegramSession)
        .where(
            TelegramSession.telegram_id == telegram_id,
            TelegramSession.is_active == True,
        )
        .values(is_active=False, token="")
    )
    await db.commit()


async def delete_all_sessions(db: AsyncSession, telegram_id: int) -> None:
    """Deletes all sessions for a user (used by /logoutall)."""
    await db.execute(
        delete(TelegramSession).where(
            TelegramSession.telegram_id == telegram_id,
        )
    )
    await db.commit()


# ── Login state ───────────────────────────────────────────────────────────────

async def set_login_state(
    db: AsyncSession,
    telegram_id: int,
    step: str,
    project: Optional[str] = None,
    email:   Optional[str] = None,
    phone:   Optional[str] = None,
) -> None:
    state = await get_login_state(db, telegram_id)
    if state is None:
        state = TelegramLoginState(telegram_id=telegram_id)
        db.add(state)
    state.step = step
    if project is not None:
        state.project = project
    if email is not None:
        state.email = email
    if phone is not None:
        state.phone = phone
    await db.commit()


async def get_login_state(db: AsyncSession, telegram_id: int) -> TelegramLoginState | None:
    result = await db.execute(
        select(TelegramLoginState).where(TelegramLoginState.telegram_id == telegram_id)
    )
    return result.scalar_one_or_none()


async def clear_login_state(db: AsyncSession, telegram_id: int) -> None:
    state = await get_login_state(db, telegram_id)
    if state:
        await db.delete(state)
        await db.commit()