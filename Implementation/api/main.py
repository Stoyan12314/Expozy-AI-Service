"""FastAPI Webhook API for Telegram updates."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import ORJSONResponse

from shared.config import get_settings
from shared.services import get_mq, close_mq, close_db
from api.telegram.service.telegram import get_telegram_client, close_telegram_client
from shared.utils import setup_logging, get_logger

from api.telegram import router
from api.exceptions import register_exception_handlers

setup_logging()
logger = get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown handler."""
    logger.info("API starting up", telegram_configured=bool(settings.telegram_bot_token))
    
    get_telegram_client()
    
    yield
    
    logger.info("API shutting down")
    await close_telegram_client()
    await close_mq()
    await close_db()


app = FastAPI(
    title="AI Orchestrator Webhook API",
    description="Telegram webhook handler for AI template generation",
    version="1.0.0",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "api"}
app.include_router(router.router, prefix="/telegram", tags=["Telegram"])
register_exception_handlers(app)