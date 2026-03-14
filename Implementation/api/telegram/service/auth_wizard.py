from __future__ import annotations

import asyncio

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import get_settings
from api.telegram.service.keyboards import LOGIN_KEYBOARD, STATUS_KEYBOARD
from api.telegram.service.messaging import send_message
from api.telegram.persistence.telegram_persistence import (
    save_user_session,
    get_user_session,
    get_session_by_project,
    set_login_state,
    get_login_state,
    clear_login_state,
)
from api.telegram.service.store_api import _api_login, _do_newstore_background

settings = get_settings()


async def start_newstore_flow(db: AsyncSession, telegram_id: int) -> tuple[str, dict]:
    await clear_login_state(db, telegram_id)
    await set_login_state(db, telegram_id, step="newstore:title")
    return (
        "🏪 *Create your Expozy storefront*\n\n"
        "Enter a *store name* — must be unique, lowercase only\n"
        "(e.g. `mystore2026`):"
    ), {}


async def start_login_flow(db: AsyncSession, telegram_id: int) -> tuple[str, dict]:
    session = await get_user_session(db, telegram_id)
    if session:
        return (
            f"✅ You're already connected to *{session.project}*\n"
            f"🌐 {session.project_url}\n\n"
            "Use /logout to disconnect, or /mystore to see all your stores."
        ), STATUS_KEYBOARD
    await clear_login_state(db, telegram_id)
    await set_login_state(db, telegram_id, step="login:project")
    return "Enter your *store name* (e.g. `mystore`):", {}


async def handle_auth_step(
    db: AsyncSession,
    telegram_id: int,
    text: str,
    *,
    chat_id: int,
    background_tasks: BackgroundTasks,
) -> tuple[str, dict] | None:
    state = await get_login_state(db, telegram_id)
    if not state:
        return None

    if text.strip().startswith("/"):
        await clear_login_state(db, telegram_id)
        return None

    step = state.step

    # ── newstore wizard ────────────────────────────────────────────────────────

    if step == "newstore:title":
        await set_login_state(db, telegram_id, step="newstore:email", project=text.strip().lower())
        return "Enter your *email*:", {}

    if step == "newstore:email":
        email = text.strip()
        if "@" not in email or "." not in email.split("@")[-1]:
            return "❌ That doesn't look like a valid email. Please enter your *email*:", {}
        await set_login_state(db, telegram_id, step="newstore:phone", project=state.project, email=email)
        return "Enter your *phone number*:", {}

    if step == "newstore:phone":
        await set_login_state(
            db, telegram_id,
            step="newstore:password",
            project=state.project,
            email=state.email,
            phone=text.strip(),
        )
        return "Choose a *password* for this store:", {}

    if step == "newstore:password":
        await clear_login_state(db, telegram_id)
        await send_message(
            chat_id,
            "⏳ Creating your store — this can take up to 2 minutes.\nI'll message you when it's ready!",
        )
        asyncio.create_task(
            _do_newstore_background(
                telegram_id=telegram_id,
                chat_id=chat_id,
                title=state.project,
                phone=state.phone,
                email=state.email,
                password=text.strip(),
            )
        )
        return None

    # ── login wizard ───────────────────────────────────────────────────────────

    if step == "login:project":
        await set_login_state(db, telegram_id, step="login:email", project=text.strip().lower())
        return "Enter your *email*:", {}

    if step == "login:email":
        email = text.strip()
        if "@" not in email or "." not in email.split("@")[-1]:
            return "❌ That doesn't look like a valid email. Please enter your *email*:", {}
        await set_login_state(db, telegram_id, step="login:password", project=state.project, email=email)
        return "Enter your *password*:", {}

    if step == "login:password":
        if not state.project:
            await clear_login_state(db, telegram_id)
            return "❌ No store name found. Use /login again.", LOGIN_KEYBOARD

        token, user_obj, error = await _api_login(
            email=state.email,
            password=text.strip(),
            project=state.project,
        )
        await clear_login_state(db, telegram_id)

        if error:
            return f"❌ Login failed: {error}", LOGIN_KEYBOARD

        existing = await get_session_by_project(db, telegram_id, state.project)
        saas_key = existing.saas_key if existing else ""
        project_url = (
            existing.project_url if existing
            else f"https://{state.project}.{settings.expozy_store_domain}"
        )

        if not saas_key:
            return (
                f"❌ Store `{state.project}` has no API key on record. Re-create it with /newstore.",
                LOGIN_KEYBOARD,
            )

        await save_user_session(
            db=db,
            telegram_id=telegram_id,
            project=state.project,
            token=token,
            saas_key=saas_key,
            project_url=project_url,
        )
        name = (user_obj.get("name") or user_obj.get("email") or state.email) if user_obj else state.email
        return (
            f"✅ Logged in as *{name}*\n"
            f"Store: `{state.project}`\n\n"
            f"Use /status to get links to your storefront and admin panel."
        ), STATUS_KEYBOARD

    return None