# from __future__ import annotations

# import asyncio
# from typing import Any
# from uuid import UUID

# import httpx
# from fastapi import BackgroundTasks
# from sqlalchemy.ext.asyncio import AsyncSession

# from api.orchestrator.models.dto import TelegramUpdate
# from shared.config import get_settings
# from shared.utils import get_logger
# from shared.services import get_mq

# from api.telegram.persistence.telegram_persistence import (
#     create_job,
#     mark_job_failed,
#     save_user_session,
#     get_user_session,
#     get_all_sessions,
#     get_session_by_project,
#     set_active_session,
#     delete_active_session,
#     delete_all_sessions,
#     set_login_state,
#     get_login_state,
#     clear_login_state,
# )
# from api.telegram.telegram_text import (
#     WELCOME_MESSAGE,
#     HELP_MESSAGE,
#     INVALID_COMMAND_MESSAGE,
#     EMPTY_PROMPT_MESSAGE,
# )

# logger = get_logger(__name__)
# settings = get_settings()

# CORE_URL = "https://core.expozy.com/api/admin"

# # ── Keyboards ─────────────────────────────────────────────────────────────────

# MAIN_MENU_KEYBOARD = {
#     "keyboard": [
#         [{"text": "/login"}, {"text": "/status"}],
#         [{"text": "/help"}, {"text": "/logout"}],
#     ],
#     "resize_keyboard": True,
#     "one_time_keyboard": False,
# }

# LOGIN_KEYBOARD = {
#     "keyboard": [[{"text": "/login"}]],
#     "resize_keyboard": True,
#     "one_time_keyboard": True,
# }

# STATUS_KEYBOARD = {
#     "keyboard": [
#         [{"text": "/status"}, {"text": "/logout"}],
#     ],
#     "resize_keyboard": True,
#     "one_time_keyboard": False,
# }


# # ── Send message ──────────────────────────────────────────────────────────────

# async def send_message(chat_id: int, text: str, keyboard: dict | None = None) -> None:
#     token = settings.telegram_bot_token
#     if not token:
#         return

#     payload: dict[str, Any] = {
#         "chat_id": chat_id,
#         "text": text[:4096],
#         "parse_mode": "Markdown",
#     }
#     if keyboard:
#         payload["reply_markup"] = keyboard

#     async with httpx.AsyncClient(timeout=30.0) as client:
#         resp = await client.post(
#             f"https://api.telegram.org/bot{token}/sendMessage",
#             json=payload,
#         )
#     if resp.status_code != 200:
#         logger.error("Telegram API error", status_code=resp.status_code, chat_id=chat_id)


# # ── Job notifications (called by worker_service) ──────────────────────────────

# async def notify_job_completed(
#     chat_id: int,
#     preview_url: str,
#     base_url: str = "",
#     *,
#     html_pages: dict[str, str] | None = None,
#     bundle_id: str | None = None,
# ) -> None:
#     if html_pages and bundle_id:
#         page_filenames = {"homepage": "index.html"}
#         lines = ["✅ Your site is ready!\n"]
#         for page_type in sorted(html_pages.keys()):
#             filename = page_filenames.get(page_type, f"{page_type}.html")
#             page_url = f"{base_url}/p/{bundle_id}/{filename}"
#             display = page_type.replace("_", " ").title()
#             lines.append(f"📄 {display}: {page_url}")
#         text = "\n".join(lines)
#     else:
#         full_url = f"{base_url}{preview_url}" if base_url else preview_url
#         text = f"✅ Your page is ready!\n\n🔗 Preview: {full_url}"

#     await send_message(chat_id, text)


# async def notify_job_failed(chat_id: int, error_message: str | None = None) -> None:
#     text = "❌ Sorry, I couldn't generate your page."
#     if error_message:
#         text += f"\n\nError: {error_message[:200]}"
#     text += "\n\nPlease try again with a different prompt."
#     await send_message(chat_id, text)


# # ── Main handler ──────────────────────────────────────────────────────────────

# async def handle_telegram_update(
#     *,
#     db: AsyncSession,
#     raw_update: dict[str, Any],
#     update: TelegramUpdate,
#     background_tasks: BackgroundTasks,
#     telegram_update_db_id=None,
# ) -> dict:
#     text        = update.get_text()
#     chat_id     = update.get_chat_id()
#     user_id     = update.get_user_id()
#     telegram_id = user_id or chat_id

#     if not text or not chat_id:
#         return {"ok": True, "message": "Ignored"}

#     t = text.strip()

#     # ── Commands ──────────────────────────────────────────────────────────────
#     if t == "/start":
#         background_tasks.add_task(send_message, chat_id, WELCOME_MESSAGE, MAIN_MENU_KEYBOARD)
#         return {"ok": True}

#     if t == "/help":
#         background_tasks.add_task(send_message, chat_id, HELP_MESSAGE, MAIN_MENU_KEYBOARD)
#         return {"ok": True}

#     if t == "/status":
#         msg, kb = await get_session_status(db, telegram_id)
#         background_tasks.add_task(send_message, chat_id, msg, kb)
#         return {"ok": True}

#     if t == "/logout":
#         msg, kb = await handle_logout(db, telegram_id)
#         background_tasks.add_task(send_message, chat_id, msg, kb)
#         return {"ok": True}

#     if t == "/logoutall":
#         await delete_all_sessions(db, telegram_id)
#         await clear_login_state(db, telegram_id)
#         background_tasks.add_task(send_message, chat_id, "👋 Disconnected all stores.", LOGIN_KEYBOARD)
#         return {"ok": True}

#     if t == "/newstore":
#         msg, kb = await start_newstore_flow(db, telegram_id)
#         background_tasks.add_task(send_message, chat_id, msg, kb)
#         return {"ok": True}

#     if t == "/login":
#         msg, kb = await start_login_flow(db, telegram_id)
#         background_tasks.add_task(send_message, chat_id, msg, kb)
#         return {"ok": True}

#     if t in ("/mystore", "/stores"):
#         msg, kb = await list_stores(db, telegram_id)
#         background_tasks.add_task(send_message, chat_id, msg, kb)
#         return {"ok": True}

#     if t.startswith("switch:"):
#         project = t[7:].strip()
#         msg, kb = await switch_store(db, telegram_id, project)
#         background_tasks.add_task(send_message, chat_id, msg, kb)
#         return {"ok": True}

#     # ── Mid-flow step (newstore / login wizard) ───────────────────────────────
#     step_result = await handle_auth_step(
#         db, telegram_id, t,
#         chat_id=chat_id,
#         background_tasks=background_tasks,
#     )
#     if step_result is not None:
#         msg, kb = step_result
#         background_tasks.add_task(send_message, chat_id, msg, kb)
#         return {"ok": True}

#     # ── /prompt ───────────────────────────────────────────────────────────────
#     if not t.startswith("/prompt"):
#         background_tasks.add_task(send_message, chat_id, INVALID_COMMAND_MESSAGE)
#         return {"ok": True}

#     prompt_text = text[7:].strip()
#     if not prompt_text:
#         background_tasks.add_task(send_message, chat_id, EMPTY_PROMPT_MESSAGE)
#         return {"ok": True}

#     session = await get_user_session(db, telegram_id)
#     if not session or not session.saas_key:
#         background_tasks.add_task(
#             send_message, chat_id,
#             "❌ No store connected. Use /newstore to create one.",
#             LOGIN_KEYBOARD,
#         )
#         return {"ok": True}

#     if telegram_update_db_id is None:
#         return {"ok": True, "message": "No update ID"}

#     job_id = await create_job(
#         db,
#         telegram_update_id=telegram_update_db_id,
#         chat_id=chat_id,
#         user_id=user_id,
#         prompt_text=prompt_text,
#     )
#     await db.commit()

#     try:
#         mq = await get_mq()
#         await mq.publish_job(job_id, attempt=1)
#     except Exception as e:
#         logger.error("Failed to publish job", job_id=str(job_id), error=str(e))
#         background_tasks.add_task(send_message, chat_id, "⚠️ Queue error. Please try again.")
#         return {"ok": True, "job_id": str(job_id)}

#     background_tasks.add_task(
#         send_message, chat_id,
#         "🔄 Working on it... I'll send you the preview link when it's ready!",
#     )
#     return {"ok": True, "job_id": str(job_id)}


# # ── New store flow ────────────────────────────────────────────────────────────

# async def start_newstore_flow(db, telegram_id: int) -> tuple[str, dict]:
#     await clear_login_state(db, telegram_id)
#     await set_login_state(db, telegram_id, step="newstore:title")
#     return (
#         "🏪 *Create your Expozy storefront*\n\n"
#         "Enter a *store name* — must be unique, lowercase only\n"
#         "(e.g. `mystore2026`):"
#     ), {}


# # ── Login flow ────────────────────────────────────────────────────────────────

# async def start_login_flow(db, telegram_id: int) -> tuple[str, dict]:
#     session = await get_user_session(db, telegram_id)
#     if session:
#         return (
#             f"✅ You're already connected to *{session.project}*\n"
#             f"🌐 {session.project_url}\n\n"
#             "Use /logout to disconnect, or /mystore to see all your stores."
#         ), STATUS_KEYBOARD
#     await clear_login_state(db, telegram_id)
#     await set_login_state(db, telegram_id, step="login:project")
#     return "Enter your *store name* (e.g. `mystore`):", {}


# # ── Multi-store helpers ───────────────────────────────────────────────────────

# async def list_stores(db, telegram_id: int) -> tuple[str, dict]:
#     sessions = await get_all_sessions(db, telegram_id)
#     if not sessions:
#         return "❌ No stores connected. Use /newstore to create one.", LOGIN_KEYBOARD

#     lines = ["🏪 *Your stores:*\n"]
#     for s in sessions:
#         marker = " ✅ *(active)*" if s.is_active else ""
#         lines.append(f"• `{s.project}`{marker}")
#     lines.append("\nTo switch, send: `switch:storename`")
#     return "\n".join(lines), {}


# async def switch_store(db, telegram_id: int, project: str) -> tuple[str, dict]:
#     found = await set_active_session(db, telegram_id, project)
#     if not found:
#         return f"❌ Store `{project}` not found. Use /mystore to see your stores.", {}
#     return f"✅ Switched to *{project}*", STATUS_KEYBOARD


# # ── Step router (newstore & login wizards) ────────────────────────────────────

# async def handle_auth_step(
#     db,
#     telegram_id: int,
#     text: str,
#     *,
#     chat_id: int,
#     background_tasks: BackgroundTasks,
# ) -> tuple[str, dict] | None:
#     state = await get_login_state(db, telegram_id)
#     if not state:
#         return None

#     if text.strip().startswith("/"):
#         await clear_login_state(db, telegram_id)
#         return None

#     step = state.step

#     if step == "newstore:title":
#         await set_login_state(db, telegram_id, step="newstore:email", project=text.strip().lower())
#         return "Enter your *email*:", {}

#     if step == "newstore:email":
#         email = text.strip()
#         if "@" not in email or "." not in email.split("@")[-1]:
#             return "❌ That doesn't look like a valid email. Please enter your *email*:", {}
#         await set_login_state(db, telegram_id, step="newstore:phone", project=state.project, email=email)
#         return "Enter your *phone number*:", {}

#     if step == "newstore:phone":
#         await set_login_state(
#             db, telegram_id,
#             step="newstore:password",
#             project=state.project,
#             email=state.email,
#             phone=text.strip(),
#         )
#         return "Choose a *password* for this store:", {}

#     if step == "newstore:password":
#         await clear_login_state(db, telegram_id)
#         # Send ⏳ immediately so it arrives before the success/failure message
#         await send_message(
#             chat_id,
#             "⏳ Creating your store — this can take up to 2 minutes.\nI'll message you when it's ready!",
#         )
#         # Fire-and-forget: runs concurrently, doesn't block the event loop or other requests
#         asyncio.create_task(
#             _do_newstore_background(
#                 telegram_id=telegram_id,
#                 chat_id=chat_id,
#                 title=state.project,
#                 phone=state.phone,
#                 email=state.email,
#                 password=text.strip(),
#             )
#         )
#         # Return None so handle_telegram_update doesn't send a duplicate message
#         return None

#     if step == "login:project":
#         await set_login_state(db, telegram_id, step="login:email", project=text.strip().lower())
#         return "Enter your *email*:", {}

#     if step == "login:email":
#         email = text.strip()
#         if "@" not in email or "." not in email.split("@")[-1]:
#             return "❌ That doesn't look like a valid email. Please enter your *email*:", {}
#         await set_login_state(db, telegram_id, step="login:password", project=state.project, email=email)
#         return "Enter your *password*:", {}

#     if step == "login:password":
#         if not state.project:
#             await clear_login_state(db, telegram_id)
#             return "❌ No store name found. Use /login again.", LOGIN_KEYBOARD

#         token, user_obj, error = await _api_login(
#             email=state.email,
#             password=text.strip(),
#             project=state.project,
#         )
#         await clear_login_state(db, telegram_id)

#         if error:
#             return f"❌ Login failed: {error}", LOGIN_KEYBOARD

#         existing = await get_session_by_project(db, telegram_id, state.project)
#         saas_key = existing.saas_key if existing else ""
#         project_url = existing.project_url if existing else f"https://{state.project}.expozy.net"

#         if not saas_key:
#             return (
#                 f"❌ Store `{state.project}` has no API key on record. Re-create it with /newstore.",
#                 LOGIN_KEYBOARD,
#             )

#         await save_user_session(
#             db=db,
#             telegram_id=telegram_id,
#             project=state.project,
#             token=token,
#             saas_key=saas_key,
#             project_url=project_url,
#         )
#         name = (user_obj.get("name") or user_obj.get("email") or state.email) if user_obj else state.email
#         return (
#             f"✅ Logged in as *{name}*\n"
#             f"Store: `{state.project}`\n\n"
#             f"Use /status to get links to your storefront and admin panel."
#         ), STATUS_KEYBOARD

#     return None


# # ── Background: create store (runs after webhook already returned 200) ────────

# async def _do_newstore_background(
#     *,
#     telegram_id: int,
#     chat_id: int,
#     title: str,
#     phone: str,
#     email: str,
#     password: str,
# ) -> None:
#     from api.orchestrator.db.session import get_db_session

#     try:
#         async with httpx.AsyncClient(timeout=280.0) as client:
#             resp = await client.post(
#                 f"{CORE_URL}/saas_telegram",
#                 json={
#                     "title": title.lower(),
#                     "email": email,
#                     "phone": phone,
#                     "password": password,
#                 },
#             )

#         logger.error("saas_telegram raw response", status=resp.status_code, body=resp.text[:500])

#         data = resp.json()

#         if data.get("status") != 1:
#             errors = data.get("errors", {})
#             if isinstance(errors, dict) and errors:
#                 error_msg = next(iter(errors.values()))
#             elif isinstance(errors, list) and errors:
#                 error_msg = errors[0]
#             else:
#                 error_msg = data.get("message") or data.get("msg") or "Unknown error"
#             await send_message(chat_id, f"❌ Failed to create store: {error_msg}", LOGIN_KEYBOARD)
#             return

#         async with get_db_session() as db:
#             await save_user_session(
#                 db=db,
#                 telegram_id=telegram_id,
#                 project=data["title"],
#                 token=data["token"],
#                 saas_key=data["saas_key"],
#                 project_url=data["url"],
#             )

#         await send_message(
#             chat_id,
#             f"✅ Store *{data['title']}* created!\n"
#             f"🌐 {data['url']}\n\n"
#             f"🔧 [Admin Dashboard](https://devadmin.expozy.com/login/{data['title']})\n\n"
#             "Send me a prompt to generate your first page.",
#             STATUS_KEYBOARD,
#         )

#     except Exception as e:
#         logger.error("Newstore background task failed", error=repr(e))
#         await send_message(
#             chat_id,
#             "❌ Store creation failed. Please try /newstore again.",
#             LOGIN_KEYBOARD,
#         )


# # ── API helpers ───────────────────────────────────────────────────────────────

# async def _api_login(
#     email: str,
#     password: str,
#     project: str,
# ) -> tuple[str, dict | None, str | None]:
#     try:
#         async with httpx.AsyncClient(timeout=15.0) as client:
#             resp = await client.post(
#                 f"{CORE_URL}/login_telegram",
#                 json={"project": project, "email": email, "password": password},
#             )
#         data = resp.json()
#         token = data.get("token", "")
#         if not token:
#             return "", None, data.get("message", "Invalid credentials")
#         user = data.get("user", {})
#         if isinstance(user, dict) and "data" in user:
#             user = user["data"].get("attributes", user)
#         return token, user, None
#     except Exception as e:
#         return "", None, str(e)


# async def handle_logout(db, telegram_id: int) -> tuple[str, dict]:
#     session = await get_user_session(db, telegram_id)
#     if not session:
#         return "❌ No active store to disconnect.", LOGIN_KEYBOARD

#     project = session.project
#     await delete_active_session(db, telegram_id)
#     await clear_login_state(db, telegram_id)

#     remaining = [s for s in await get_all_sessions(db, telegram_id) if s.project != project]
#     if remaining:
#         await set_active_session(db, telegram_id, remaining[0].project)
#         return (
#             f"👋 Disconnected *{project}*.\n"
#             f"Switched to *{remaining[0].project}*.\n\n"
#             "Use /mystore to see all your stores."
#         ), STATUS_KEYBOARD

#     return f"👋 Disconnected *{project}*.", LOGIN_KEYBOARD


# async def get_session_status(db, telegram_id: int) -> tuple[str, dict]:
#     session = await get_user_session(db, telegram_id)
#     if not session:
#         return "❌ No store connected. Use /newstore.", LOGIN_KEYBOARD

#     all_sessions = await get_all_sessions(db, telegram_id)
#     extra = f"\n\n_You have {len(all_sessions)} store(s). Use /mystore to switch._" if len(all_sessions) > 1 else ""
#     return (
#         f"✅ Connected to *{session.project}*\n"
#         f"🌐 {session.project_url}\n"
#         f"🔧 [Admin Dashboard](https://devadmin.expozy.com/login/{session.project})"
#         f"{extra}"
#     ), STATUS_KEYBOARD