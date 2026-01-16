"""
Telegram webhook: POST /telegram/webhook

Goal: respond fast and push work to the queue.

What it does:
- Check secret header
- Read + validate Telegram update
- Ignore if no text
- Handle /start and /help
- Require /prompt <text>
- Deduplicate by update_id (DB unique)
- Create a Job (status=queued)
- Publish {job_id} to RabbitMQ
- Return {"ok": true} right away
- Send "Working on it..." in background
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Depends, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import get_settings
from api.orchestrator.db.models import Job, TelegramUpdate as TelegramUpdateDB
from api.orchestrator.models.schemas import TelegramUpdate, WebhookResponse, ErrorResponse
from shared.services import get_session, get_mq
from api.telegram.service.telegram import notify_job_started, send_telegram_message
from shared.utils import get_logger

from api.telegram.telegramBotSetUp import (
    WELCOME_MESSAGE,
    HELP_MESSAGE,
    INVALID_COMMAND_MESSAGE,
    EMPTY_PROMPT_MESSAGE,
)

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
    # 1) Secret check
    if x_telegram_bot_api_secret_token != settings.telegram_secret_token:
        raise HTTPException(status_code=401, detail="Invalid secret token")

    # 2) Parse JSON
    try:
        raw_update: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # 3) Validate update shape
    try:
        update = TelegramUpdate.model_validate(raw_update)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Telegram update format")

    update_id = update.update_id
    text = update.get_text()
    chat_id = update.get_chat_id()
    user_id = update.get_user_id()

    # Ignore updates that don’t contain a text message
    if not text or not chat_id:
        return WebhookResponse(ok=True, message="Ignored")

    # 4) Commands
    cmd_response = await handle_commands(text, chat_id, update_id, background_tasks)
    if cmd_response:
        return cmd_response

    # 5) Must be /prompt <something>
    prompt_text = text[7:].strip()  # remove "/prompt "
    if not prompt_text:
        background_tasks.add_task(send_telegram_message, chat_id=chat_id, text=EMPTY_PROMPT_MESSAGE)
        return WebhookResponse(ok=True, message="Empty prompt")

    # 6) Deduplicate by update_id
    ins = (
        insert(TelegramUpdateDB)
        .values(update_id=update_id, raw_update=raw_update)
        .on_conflict_do_nothing(index_elements=["update_id"])
        .returning(TelegramUpdateDB.id)
    )
    result = await db.execute(ins)
    row = result.fetchone()

    # If already seen, return existing job if it exists
    if row is None:
        return await handle_duplicate(db, update_id)

    telegram_update_db_id = row[0]

    # 7) Create job row
    job = Job(
        telegram_update_id=telegram_update_db_id,
        chat_id=chat_id,
        user_id=user_id,
        prompt_text=prompt_text,
        status="queued",
    )
    db.add(job)
    await db.flush()  # job.id becomes available
    job_id: UUID = job.id

    # 8) Publish job_id only
    try:
        mq = await get_mq()
        await mq.publish_job(job_id, attempt=1)
    except Exception as e:
        logger.error("Failed to publish job", job_id=str(job_id), error=str(e))

    # 9) Tell user “working” in background
    background_tasks.add_task(notify_job_started, chat_id=chat_id, job_id=str(job_id))

    # Return fast
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
