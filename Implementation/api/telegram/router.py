"""
Telegram webhook: POST /telegram/webhook

Steps:
- Check secret header
- Read + validate Telegram update
- Ignore if no text
- Handle /start and /help
- Require /prompt <text>
- Deduplicate by update_id
- Create Job (queued)
- COMMIT (so worker can see it)
- Publish job_id to RabbitMQ
- Return {"ok": true} fast
- Send "Working on it..." in background
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.orchestrator.db.models import Job, TelegramUpdate as TelegramUpdateDB
from api.orchestrator.models.schemas import ErrorResponse, TelegramUpdate, WebhookResponse
from api.telegram.service.telegram import notify_job_started, send_telegram_message
from api.telegram.telegramBotSetUp import (
    EMPTY_PROMPT_MESSAGE,
    HELP_MESSAGE,
    INVALID_COMMAND_MESSAGE,
    WELCOME_MESSAGE,
)
from shared.config import get_settings
from shared.services import get_mq, get_session
from shared.utils import get_logger

router = APIRouter()
logger = get_logger(__name__)
settings = get_settings()


@router.post(
    "/webhook",
    response_model=WebhookResponse,
    responses={
        401: {"model": ErrorResponse},
        400: {"model": ErrorResponse},
    },
)
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str = Header(..., alias="X-Telegram-Bot-Api-Secret-Token"),
    db: AsyncSession = Depends(get_session),
) -> WebhookResponse:
    # 1) secret check
    if x_telegram_bot_api_secret_token != settings.telegram_secret_token:
        raise HTTPException(status_code=401, detail="Invalid secret token")

    # 2) parse json
    try:
        raw_update: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # 3) validate update
    try:
        update = TelegramUpdate.model_validate(raw_update)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Telegram update format")

    update_id = update.update_id
    text = update.get_text()
    chat_id = update.get_chat_id()
    user_id = update.get_user_id()

    # ignore non-text messages
    if not text or not chat_id:
        return WebhookResponse(ok=True, message="Ignored")

    # 4) commands
    cmd_response = await handle_commands(text, chat_id, update_id, background_tasks)
    if cmd_response:
        return cmd_response

    # 5) require /prompt <text>
    prompt_text = text[7:].strip()  # remove "/prompt "
    if not prompt_text:
        background_tasks.add_task(send_telegram_message, chat_id=chat_id, text=EMPTY_PROMPT_MESSAGE)
        return WebhookResponse(ok=True, message="Empty prompt")

    # 6) dedupe by update_id
    ins = (
        insert(TelegramUpdateDB)
        .values(update_id=update_id, raw_update=raw_update)
        .on_conflict_do_nothing(index_elements=["update_id"])
        .returning(TelegramUpdateDB.id)
    )
    result = await db.execute(ins)
    row = result.fetchone()

    if row is None:
        return await handle_duplicate(db, update_id)

    telegram_update_db_id = row[0]

    # 7) create job
    job = Job(
        telegram_update_id=telegram_update_db_id,
        chat_id=chat_id,
        user_id=user_id,
        prompt_text=prompt_text,
        status="queued",
    )
    db.add(job)
    await db.flush()
    job_id: UUID = job.id

    # 8) ✅ commit first (important)
    await db.commit()

    # 9) publish after commit
    try:
        mq = await get_mq()
        await mq.publish_job(job_id, attempt=1)
    except Exception as e:
        logger.error("Failed to publish job", job_id=str(job_id), error=str(e))

        # mark failed so it doesn't sit queued forever
        try:
            job.status = "failed"
            job.error_message = f"Failed to enqueue job: {e}"
            await db.commit()
        except Exception:
            pass

        background_tasks.add_task(
            send_telegram_message,
            chat_id=chat_id,
            text="⚠️ Queue error. Please try again.",
        )
        return WebhookResponse(ok=True, job_id=job_id, message="Queue publish failed")

    # 10) tell user we started
    background_tasks.add_task(notify_job_started, chat_id=chat_id, job_id=str(job_id))

    # return fast
    return WebhookResponse(ok=True, job_id=job_id)


async def handle_commands(
    text: str,
    chat_id: int,
    update_id: int,
    background_tasks: BackgroundTasks,
) -> WebhookResponse | None:
    t = text.strip()

    if t == "/start":
        background_tasks.add_task(send_telegram_message, chat_id=chat_id, text=WELCOME_MESSAGE)
        return WebhookResponse(ok=True, message="Start")

    if t == "/help":
        background_tasks.add_task(send_telegram_message, chat_id=chat_id, text=HELP_MESSAGE)
        return WebhookResponse(ok=True, message="Help")

    if not text.startswith("/prompt"):
        background_tasks.add_task(send_telegram_message, chat_id=chat_id, text=INVALID_COMMAND_MESSAGE)
        logger.debug("Invalid command", update_id=update_id, text=text[:50])
        return WebhookResponse(ok=True, message="Invalid command")

    return None


async def handle_duplicate(db: AsyncSession, update_id: int) -> WebhookResponse:
    q = (
        select(Job)
        .join(TelegramUpdateDB, Job.telegram_update_id == TelegramUpdateDB.id)
        .where(TelegramUpdateDB.update_id == update_id)
    )
    result = await db.execute(q)
    job = result.scalar_one_or_none()

    if job:
        return WebhookResponse(ok=True, job_id=job.id, message="Already processing")

    return WebhookResponse(ok=True, message="Already received")
