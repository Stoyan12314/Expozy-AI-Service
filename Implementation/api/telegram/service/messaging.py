from __future__ import annotations

from typing import Any

import httpx

from shared.config import get_settings
from shared.utils import get_logger

logger = get_logger(__name__)
settings = get_settings()


async def send_message(chat_id: int, text: str, keyboard: dict | None = None) -> None:
    token = settings.telegram_bot_token
    if not token:
        return

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": "Markdown",
    }
    if keyboard:
        payload["reply_markup"] = keyboard

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
        )
    if resp.status_code != 200:
        logger.error("Telegram API error", status_code=resp.status_code, chat_id=chat_id)


async def notify_job_completed(
    chat_id: int,
    preview_url: str,
    base_url: str = "",
    *,
    html_pages: dict[str, str] | None = None,
    bundle_id: str | None = None,
) -> None:
    if html_pages and bundle_id:
        page_filenames = {"homepage": "index.html"}
        lines = ["✅ Your site is ready!\n"]
        for page_type in sorted(html_pages.keys()):
            filename = page_filenames.get(page_type, f"{page_type}.html")
            page_url = f"{base_url}/p/{bundle_id}/{filename}"
            display = page_type.replace("_", " ").title()
            lines.append(f"📄 {display}: {page_url}")
        text = "\n".join(lines)
    else:
        full_url = f"{base_url}{preview_url}" if base_url else preview_url
        text = f"✅ Your page is ready!\n\n🔗 Preview: {full_url}"

    await send_message(chat_id, text)


async def notify_job_failed(chat_id: int, error_message: str | None = None) -> None:
    text = "❌ Sorry, I couldn't generate your page."
    if error_message:
        text += f"\n\nError: {error_message[:200]}"
    text += "\n\nPlease try again with a different prompt."
    await send_message(chat_id, text)