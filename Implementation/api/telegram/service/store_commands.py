from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import get_settings
from api.telegram.service.keyboards import LOGIN_KEYBOARD, STATUS_KEYBOARD
from api.telegram.persistence.telegram_persistence import (
    get_user_session,
    get_all_sessions,
    set_active_session,
    delete_active_session,
    clear_login_state,
)

settings = get_settings()


async def list_stores(db: AsyncSession, telegram_id: int) -> tuple[str, dict]:
    sessions = await get_all_sessions(db, telegram_id)
    if not sessions:
        return "❌ No stores connected. Use /newstore to create one.", LOGIN_KEYBOARD

    lines = ["🏪 *Your stores:*\n"]
    for s in sessions:
        marker = " ✅ *(active)*" if s.is_active else ""
        lines.append(f"• `{s.project}`{marker}")
    lines.append("\nTo switch, send: `switch:storename`")
    return "\n".join(lines), {}


async def switch_store(db: AsyncSession, telegram_id: int, project: str) -> tuple[str, dict]:
    found = await set_active_session(db, telegram_id, project)
    if not found:
        return f"❌ Store `{project}` not found. Use /mystore to see your stores.", {}
    return f"✅ Switched to *{project}*", STATUS_KEYBOARD


async def handle_logout(db: AsyncSession, telegram_id: int) -> tuple[str, dict]:
    session = await get_user_session(db, telegram_id)
    if not session:
        return "❌ No active store to disconnect.", LOGIN_KEYBOARD

    project = session.project
    await delete_active_session(db, telegram_id)
    await clear_login_state(db, telegram_id)

    remaining = [s for s in await get_all_sessions(db, telegram_id) if s.project != project]
    if remaining:
        await set_active_session(db, telegram_id, remaining[0].project)
        return (
            f"👋 Disconnected *{project}*.\n"
            f"Switched to *{remaining[0].project}*.\n\n"
            "Use /mystore to see all your stores."
        ), STATUS_KEYBOARD

    return f"👋 Disconnected *{project}*.", LOGIN_KEYBOARD


async def get_session_status(db: AsyncSession, telegram_id: int) -> tuple[str, dict]:
    session = await get_user_session(db, telegram_id)
    if not session:
        return "❌ No store connected. Use /newstore.", LOGIN_KEYBOARD

    all_sessions = await get_all_sessions(db, telegram_id)
    extra = (
        f"\n\n_You have {len(all_sessions)} store(s). Use /mystore to switch._"
        if len(all_sessions) > 1
        else ""
    )
    return (
        f"✅ Connected to *{session.project}*\n"
        f"🌐 {session.project_url}\n"
        f"🔧 [Admin Dashboard]({settings.expozy_admin_login_url}/{session.project})"
        f"{extra}"
    ), STATUS_KEYBOARD