# EXPOZY Telegram Bot

Forwards prompts to AI orchestrator and returns preview links.

```
telegram-bot/
├── bot/
│   ├── handlers/
│   │   ├── prompt.py       # /prompt → send to orchestrator
│   │   ├── auth.py         # (placeholder)
│   │   └── status.py       # /start, /help, /status, /cancel
│   ├── services/
│   │   ├── orchestrator.py # HTTP client for AI backend
│   │   └── shop_lookup.py  # (placeholder)
│   ├── config.py
│   └── main.py             # FastAPI webhook server
├── tests/
├── Dockerfile
├── requirements.txt
└── .env.example
```

## What it does

```
User: /prompt Create a website for cars
         ↓
Bot: POST to orchestrator { user_id, chat_id, prompt }
         ↓
Orchestrator: { preview_url: "https://..." }
         ↓
User: ✅ Your page is ready! 🔗 View Preview
```

## Run

```bash
pip install -r requirements.txt

export TELEGRAM_BOT_TOKEN=xxx
export WEBHOOK_URL=https://bot.yourdomain.com
export ORCHESTRATOR_URL=http://your-api/api/generate

python -m bot.main
```

## Orchestrator API

**Request:**
```json
POST /api/generate
{
  "telegram_user_id": "123456",
  "telegram_chat_id": "123456",
  "prompt": "Create a website for cars"
}
```

**Response:**
```json
{
  "preview_url": "https://preview.expozy.bg/abc123"
}
```

## Docker

```bash
docker build -t expozy-bot .
docker run -p 8443:8443 \
  -e TELEGRAM_BOT_TOKEN=xxx \
  -e WEBHOOK_URL=https://bot.yourdomain.com \
  -e ORCHESTRATOR_URL=http://api/generate \
  expozy-bot
```
