import os


class Config:
    BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    
    # Webhook
    WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")
    PORT = int(os.getenv("PORT", "8443"))
    
    # Orchestrator
    ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8080/api/generate")
    ORCHESTRATOR_TIMEOUT = int(os.getenv("ORCHESTRATOR_TIMEOUT", "120"))
