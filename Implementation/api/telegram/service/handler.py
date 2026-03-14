from __future__ import annotations

from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from api.orchestrator.models.dto import TelegramUpdate
from shared.utils import get_logger
from shared.services import get_mq

from api.telegram.persistence.telegram_persistence import (
    create_job,
    get_user_session,
    delete_all_sessions,
    clear_login_state,
)
from api.telegram.telegram_text import (
    WELCOME_MESSAGE,
    HELP_MESSAGE,
    INVALID_COMMAND_MESSAGE,
    EMPTY_PROMPT_MESSAGE,
)
from api.telegram.service.keyboards import MAIN_MENU_KEYBOARD, LOGIN_KEYBOARD
from api.telegram.service.messaging import send_message
from api.telegram.service.auth_wizard import start_newstore_flow, start_login_flow, handle_auth_step
from api.telegram.service.store_commands import list_stores, switch_store, handle_logout, get_session_status

logger = get_logger(__name__)


async def handle_telegram_update(
    *,
    db: AsyncSession,
    raw_update: dict[str, Any],
    update: TelegramUpdate,
    background_tasks: BackgroundTasks,
    telegram_update_db_id=None,
) -> dict:
    text        = update.get_text()
    chat_id     = update.get_chat_id()
    user_id     = update.get_user_id()
    telegram_id = user_id or chat_id

    if not text or not chat_id:
        return {"ok": True, "message": "Ignored"}

    t = text.strip()

    # ── Commands ──────────────────────────────────────────────────────────────

    if t == "/start":
        background_tasks.add_task(send_message, chat_id, WELCOME_MESSAGE, MAIN_MENU_KEYBOARD)
        return {"ok": True}

    if t == "/help":
        background_tasks.add_task(send_message, chat_id, HELP_MESSAGE, MAIN_MENU_KEYBOARD)
        return {"ok": True}

    if t == "/status":
        msg, kb = await get_session_status(db, telegram_id)
        background_tasks.add_task(send_message, chat_id, msg, kb)
        return {"ok": True}

    if t == "/logout":
        msg, kb = await handle_logout(db, telegram_id)
        background_tasks.add_task(send_message, chat_id, msg, kb)
        return {"ok": True}

    if t == "/logoutall":
        await delete_all_sessions(db, telegram_id)
        await clear_login_state(db, telegram_id)
        background_tasks.add_task(send_message, chat_id, "👋 Disconnected all stores.", LOGIN_KEYBOARD)
        return {"ok": True}

    if t == "/newstore":
        msg, kb = await start_newstore_flow(db, telegram_id)
        background_tasks.add_task(send_message, chat_id, msg, kb)
        return {"ok": True}

    if t == "/login":
        msg, kb = await start_login_flow(db, telegram_id)
        background_tasks.add_task(send_message, chat_id, msg, kb)
        return {"ok": True}

    if t in ("/mystore", "/stores"):
        msg, kb = await list_stores(db, telegram_id)
        background_tasks.add_task(send_message, chat_id, msg, kb)
        return {"ok": True}

    if t.startswith("switch:"):
        project = t[7:].strip()
        msg, kb = await switch_store(db, telegram_id, project)
        background_tasks.add_task(send_message, chat_id, msg, kb)
        return {"ok": True}

    # ── Mid-flow step (newstore / login wizard) ───────────────────────────────

    step_result = await handle_auth_step(
        db, telegram_id, t,
        chat_id=chat_id,
        background_tasks=background_tasks,
    )
    if step_result is not None:
        msg, kb = step_result
        background_tasks.add_task(send_message, chat_id, msg, kb)
        return {"ok": True}

    # ── /prompt ───────────────────────────────────────────────────────────────

    if not t.startswith("/prompt"):
        background_tasks.add_task(send_message, chat_id, INVALID_COMMAND_MESSAGE)
        return {"ok": True}

    prompt_text = text[7:].strip()
    if not prompt_text:
        background_tasks.add_task(send_message, chat_id, EMPTY_PROMPT_MESSAGE)
        return {"ok": True}

    session = await get_user_session(db, telegram_id)
    if not session or not session.saas_key:
        background_tasks.add_task(
            send_message, chat_id,
            "❌ No store connected. Use /newstore to create one.",
            LOGIN_KEYBOARD,
        )
        return {"ok": True}

    if telegram_update_db_id is None:
        return {"ok": True, "message": "No update ID"}

    job_id = await create_job(
        db,
        telegram_update_id=telegram_update_db_id,
        chat_id=chat_id,
        user_id=user_id,
        prompt_text=prompt_text,
    )
    await db.commit()

    try:
        mq = await get_mq()
        await mq.publish_job(job_id, attempt=1)
    except Exception as e:
        logger.error("Failed to publish job", job_id=str(job_id), error=str(e))
        background_tasks.add_task(send_message, chat_id, "⚠️ Queue error. Please try again.")
        return {"ok": True, "job_id": str(job_id)}

    background_tasks.add_task(
        send_message, chat_id,
        "🔄 Working on it... I'll send you the preview link when it's ready!",
    )
    return {"ok": True, "job_id": str(job_id)}