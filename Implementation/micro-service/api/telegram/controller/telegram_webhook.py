from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.orchestrator.models.dto import ErrorResponse, TelegramUpdate, WebhookResponse
from shared.config import get_settings
from shared.services import get_session
from shared.utils import get_logger

from api.telegram.service.telegram_service import handle_telegram_update
from api.telegram.telegram_client import notify_job_started, send_telegram_message
from api.telegram.telegram_text import (
    EMPTY_PROMPT_MESSAGE,
    HELP_MESSAGE,
    INVALID_COMMAND_MESSAGE,
    WELCOME_MESSAGE,
)

router = APIRouter()
logger = get_logger(__name__)
settings = get_settings()


_MESSAGE_MAP = {
    "WELCOME_MESSAGE": WELCOME_MESSAGE,
    "HELP_MESSAGE": HELP_MESSAGE,
    "INVALID_COMMAND_MESSAGE": INVALID_COMMAND_MESSAGE,
    "EMPTY_PROMPT_MESSAGE": EMPTY_PROMPT_MESSAGE,
}


@router.post(
    "/webhook",
    response_model=WebhookResponse,
    responses={401: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
)
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str = Header(..., alias="X-Telegram-Bot-Api-Secret-Token"),
    db: AsyncSession = Depends(get_session),
) -> WebhookResponse:
    # controller concern: auth/header check
    if x_telegram_bot_api_secret_token != settings.telegram_secret_token:
        raise HTTPException(status_code=401, detail="Invalid secret token")

    # controller concern: request parsing
    try:
        raw_update: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # controller concern: DTO validation
    try:
        update = TelegramUpdate.model_validate(raw_update)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Telegram update format")

    decision = await handle_telegram_update(db=db, raw_update=raw_update, update=update)

    # controller triggers side effects (send messages) via background tasks
    if decision.send_text:
        background_tasks.add_task(
            send_telegram_message,
            chat_id=update.get_chat_id(),
            text=_MESSAGE_MAP.get(decision.send_text, decision.send_text),
        )

    if decision.notify_started and update.get_chat_id():
        background_tasks.add_task(
            notify_job_started,
            chat_id=update.get_chat_id(),
            job_id=str(decision.job_id) if decision.job_id else "",
        )

    return WebhookResponse(ok=decision.ok, job_id=decision.job_id, message=decision.message)
