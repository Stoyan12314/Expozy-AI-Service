from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.orchestrator.models.dto import TelegramUpdate 
from shared.config import get_settings
from shared.utils import get_logger
from shared.services import get_mq



from api.telegram.persistence.telegram_persistence import (
    insert_telegram_update_dedup,
    find_job_by_update_id,
    create_job,
    mark_job_failed,
)

logger = get_logger(__name__)
settings = get_settings()


@dataclass(frozen=True)
class WebhookDecision:
    ok: bool = True
    job_id: Optional[UUID] = None
    message: Optional[str] = None

    # instructions for controller (side effects)
    send_text: Optional[str] = None
    notify_started: bool = False


def _extract_prompt(text: str) -> Optional[str]:
    if not text.startswith("/prompt"):
        return None
    prompt_text = text[7:].strip()  # remove "/prompt "
    return prompt_text or ""


async def handle_telegram_update(
    *,
    db: AsyncSession,
    raw_update: dict[str, Any],
    update: TelegramUpdate,
) -> WebhookDecision:
    """
    Business logic:
    - ignore non-text
    - commands
    - require /prompt
    - dedupe by update_id
    - create job + commit
    - publish to RabbitMQ after commit
    """
    update_id = update.update_id
    text = update.get_text()
    chat_id = update.get_chat_id()
    user_id = update.get_user_id()

    if not text or not chat_id:
        return WebhookDecision(message="Ignored")

    t = text.strip()
    if t == "/start":
        return WebhookDecision(message="Start", send_text="WELCOME_MESSAGE")  # controller maps to real constant
    if t == "/help":
        return WebhookDecision(message="Help", send_text="HELP_MESSAGE")

    prompt_text = _extract_prompt(text)
    if prompt_text is None:
        return WebhookDecision(message="Invalid command", send_text="INVALID_COMMAND_MESSAGE")

    if prompt_text == "":
        return WebhookDecision(message="Empty prompt", send_text="EMPTY_PROMPT_MESSAGE")

    # dedupe insert telegram update
    telegram_update_db_id = await insert_telegram_update_dedup(
        db,
        update_id=update_id,
        raw_update=raw_update,
    )

    if telegram_update_db_id is None:
        # duplicate; try find job and return it
        job = await find_job_by_update_id(db, update_id)
        if job:
            return WebhookDecision(job_id=job.id, message="Already processing")
        return WebhookDecision(message="Already received")

    # create job + commit so worker can see it
    job_id = await create_job(
        db,
        telegram_update_id=telegram_update_db_id,
        chat_id=chat_id,
        user_id=user_id,
        prompt_text=prompt_text,
    )

    await db.commit()

    # publish after commit
    try:
        mq = await get_mq()
        await mq.publish_job(job_id, attempt=1)
    except Exception as e:
        logger.error("Failed to publish job", job_id=str(job_id), error=str(e))

        # mark failed so it doesn't stay queued forever
        try:
            job = await find_job_by_update_id(db, update_id)
            if job:
                await mark_job_failed(db, job, error_message=f"Failed to enqueue job: {e}")
                await db.commit()
        except Exception:
            pass

        return WebhookDecision(job_id=job_id, message="Queue publish failed", send_text="⚠️ Queue error. Please try again.")

    return WebhookDecision(job_id=job_id, notify_started=True)
