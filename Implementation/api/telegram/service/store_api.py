from __future__ import annotations

import httpx

from shared.config import get_settings
from shared.utils import get_logger

from api.telegram.service.keyboards import LOGIN_KEYBOARD, STATUS_KEYBOARD
from api.telegram.service.messaging import send_message
from api.telegram.persistence.telegram_persistence import save_user_session

logger = get_logger(__name__)
settings = get_settings()


async def _api_login(
    email: str,
    password: str,
    project: str,
) -> tuple[str, dict | None, str | None]:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                settings.core_login_telegram_url,
                json={"project": project, "email": email, "password": password},
            )
        data = resp.json()
        token = data.get("token", "")
        if not token:
            return "", None, data.get("message", "Invalid credentials")
        user = data.get("user", {})
        if isinstance(user, dict) and "data" in user:
            user = user["data"].get("attributes", user)
        return token, user, None
    except Exception as e:
        return "", None, str(e)


async def _do_newstore_background(
    *,
    telegram_id: int,
    chat_id: int,
    title: str,
    phone: str,
    email: str,
    password: str,
) -> None:
    from api.orchestrator.db.session import get_db_session

    try:
        async with httpx.AsyncClient(timeout=280.0) as client:
            resp = await client.post(
                settings.core_saas_telegram_url,
                json={
                    "title": title.lower(),
                    "email": email,
                    "phone": phone,
                    "password": password,
                },
            )

        logger.debug("saas_telegram raw response", status=resp.status_code, body=resp.text[:500])

        data = resp.json()

        if data.get("status") != 1:
            errors = data.get("errors", {})
            if isinstance(errors, dict) and errors:
                error_msg = next(iter(errors.values()))
            elif isinstance(errors, list) and errors:
                error_msg = errors[0]
            else:
                error_msg = data.get("message") or data.get("msg") or "Unknown error"
            await send_message(chat_id, f"❌ Failed to create store: {error_msg}", LOGIN_KEYBOARD)
            return

        async with get_db_session() as db:
            await save_user_session(
                db=db,
                telegram_id=telegram_id,
                project=data["title"],
                token=data["token"],
                saas_key=data["saas_key"],
                project_url=data["url"],
            )

        await send_message(
            chat_id,
            f"✅ Store *{data['title']}* created!\n"
            f"🌐 {data['url']}\n\n"
            f"🔧 [Admin Dashboard]({settings.expozy_admin_login_url}/{data['title']})\n\n"
            "Send me a prompt to generate your first page.",
            STATUS_KEYBOARD,
        )

    except Exception as e:
        logger.error("Newstore background task failed", error=repr(e))
        await send_message(
            chat_id,
            "❌ Store creation failed. Please try /newstore again.",
            LOGIN_KEYBOARD,
        )