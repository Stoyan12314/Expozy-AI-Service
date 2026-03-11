from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.orchestrator.models.dto import TelegramUpdate, WebhookResponse
from shared.config import get_settings
from shared.services import get_session
from shared.utils import get_logger

from api.telegram.service.telegram_service import handle_telegram_update
from api.telegram.persistence.telegram_persistence import insert_telegram_update_dedup

router = APIRouter()
logger = get_logger(__name__)
settings = get_settings()


@router.post("/webhook", response_model=WebhookResponse)
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str = Header(..., alias="X-Telegram-Bot-Api-Secret-Token"),
    db: AsyncSession = Depends(get_session),
) -> WebhookResponse:
    if x_telegram_bot_api_secret_token != settings.telegram_secret_token:
        raise HTTPException(status_code=401, detail="Invalid secret token")

    try:
        raw_update: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    try:
        update = TelegramUpdate.model_validate(raw_update)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Telegram update format")

    # Deduplicate retried Telegram deliveries
    update_id = raw_update.get("update_id")
    telegram_update_db_id = None
    if update_id:
        telegram_update_db_id = await insert_telegram_update_dedup(db, update_id=update_id, raw_update=raw_update)
        await db.commit()
        if telegram_update_db_id is None:
            return WebhookResponse(ok=True, message="Duplicate")

    result = await handle_telegram_update(
        db=db,
        raw_update=raw_update,
        update=update,
        background_tasks=background_tasks,
        telegram_update_db_id=telegram_update_db_id,
    )

    return WebhookResponse(ok=result.get("ok", True), message=result.get("message"))