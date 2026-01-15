import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application, CommandHandler

from bot.config import Config
from bot.handlers import (
    start_command,
    help_command,
    prompt_command,
    status_command,
    cancel_command,
)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

ptb_app: Application = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ptb_app
    
    # Build Telegram app
    ptb_app = (
        Application.builder()
        .token(Config.BOT_TOKEN)
        .updater(None)
        .build()
    )
    
    # Register handlers
    ptb_app.add_handler(CommandHandler("start", start_command))
    ptb_app.add_handler(CommandHandler("help", help_command))
    ptb_app.add_handler(CommandHandler("prompt", prompt_command))
    ptb_app.add_handler(CommandHandler("status", status_command))
    ptb_app.add_handler(CommandHandler("cancel", cancel_command))
    
    await ptb_app.initialize()
    await ptb_app.start()
    
    # Set webhook
    webhook_url = f"{Config.WEBHOOK_URL}/webhook"
    await ptb_app.bot.set_webhook(url=webhook_url, secret_token=Config.WEBHOOK_SECRET)
    logger.info(f"✅ Webhook: {webhook_url}")
    
    yield
    
    await ptb_app.stop()
    await ptb_app.shutdown()


app = FastAPI(lifespan=lifespan)


@app.post("/webhook")
async def webhook(request: Request):
    """Receive Telegram updates"""
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != Config.WEBHOOK_SECRET:
        return Response(status_code=403)
    
    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)
    await ptb_app.process_update(update)
    return Response(status_code=200)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    logger.info(f"🤖 Port: {Config.PORT}")
    logger.info(f"📡 Webhook: {Config.WEBHOOK_URL}")
    logger.info(f"🔗 Orchestrator: {Config.ORCHESTRATOR_URL}")
    uvicorn.run(app, host="0.0.0.0", port=Config.PORT)
