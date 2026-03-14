# EXPOZY AI Orchestrator

Telegram bot-driven AI website generation system. Users send a prompt via Telegram and receive a generated EXPOZY template with a clickable preview link.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Telegram   │────▶│     API      │────▶│   RabbitMQ   │
│   Webhook    │     │  (FastAPI)   │     │    Queue     │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                                                  ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Preview    │◀────│    Worker    │◀────│  PostgreSQL  │
│  (FastAPI)   │     │   (Python)   │     │   Database   │
└──────────────┘     └──────────────┘     └──────────────┘
                             │
                             ▼
                      ┌──────────────┐
                      │  DashScope   │
                      │  (Qwen LLM)  │
                      └──────────────┘
```

## Services

| Service | Port | Description |
|---|---|---|
| **api** | 8000 | Telegram webhook handler + job creation |
| **worker** | — | Background AI generation + publishing |
| **preview** | 8001 | Static file server for generated pages |
| **postgres** | 5432 | Job persistence |
| **rabbitmq** | 5672, 15672 | Job queue |

---

## Local Development

### Prerequisites

- Docker and Docker Compose v2+
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- DashScope API key (Alibaba Cloud)
- DashVector API key (Alibaba Cloud)

### Step 1: Clone and configure

```bash
git clone <repo-url>
cd ai-orchestrator
cp .env.example .env
```

### Step 2: Fill in `.env`

The minimum required values to get running locally:

```bash
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_SECRET_TOKEN=your_random_secret   # openssl rand -hex 32
PREVIEW_BASE_URL=http://localhost:8001
DASHSCOPE_API_KEY=your_dashscope_key
DASHVECTOR_API_KEY=your_dashvector_key
DASHVECTOR_ENDPOINT=your_dashvector_endpoint
```

### Step 3: Start services

```bash
docker compose up -d
docker compose ps
```

### Step 4: Run migrations

```bash
docker compose run --rm migrations
```

### Step 5: Verify

```bash
# API health
curl http://localhost:8000/health

# Preview health
curl http://localhost:8001/health

# RabbitMQ management UI
open http://localhost:15672
# Login: orchestrator / orchestrator_secret
```

### Step 6: Test the webhook locally

```bash
curl -X POST http://localhost:8000/telegram/webhook \
  -H "Content-Type: application/json" \
  -H "X-Telegram-Bot-Api-Secret-Token: random_secret" \
  -d '{
    "update_id": 123456789,
    "message": {
      "message_id": 1,
      "date": 1234567890,
      "chat": {"id": 12345, "type": "private"},
      "from": {"id": 12345, "is_bot": false, "first_name": "Test"},
      "text": "/prompt build me a homepage for a café in Sofia"
    }
  }'
```

### Step 7: Register Telegram webhook (with ngrok)

```bash
ngrok http 8000

curl -X POST "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-ngrok-url.ngrok.io/telegram/webhook",
    "secret_token": "your_random_secret",
    "allowed_updates": ["message"],
    "drop_pending_updates": true
  }'
```

### Useful commands

```bash
# Stop everything
docker compose down

# Stop and wipe all data
docker compose down -v

# Rebuild after code changes
docker compose up -d --build

# Follow worker logs
docker compose logs -f worker

# Access PostgreSQL
docker compose exec postgres psql -U orchestrator -d orchestrator

# List generated previews
docker compose exec worker ls -la /previews/
```

---

## CI/CD Pipeline

The project uses GitHub Actions. The pipeline runs on every push and pull request.

### Jobs

```
lint → test → catalog
```

**lint** — runs `ruff check`, `ruff format --check`, and `pyright` on every push.

**test** — runs the full unit test suite with `coverage`. Requires 70% coverage to pass. All external services (DashScope, Telegram, RabbitMQ, Expozy Core API) are replaced with fakes and stubs so no live environment is needed.

**catalog** — runs only after tests pass. Checks whether `component_catalog.json` or `page_types.json` changed in the commit. If yes, runs `combine_catalog.py` then `catalog_vectorizer.py` and commits the updated `combined_catalog.json` back automatically.

### Required GitHub secrets

All values from `.env` must be added to **GitHub → Settings → Secrets → Actions**:

```
DATABASE_URL
RABBITMQ_URL
JOB_QUEUE_NAME
TELEGRAM_BOT_TOKEN
TELEGRAM_SECRET_TOKEN
TELEGRAM_SEND_MESSAGE_URL
PREVIEW_BASE_URL
PREVIEWS_PATH
AI_PROVIDER
AI_MODEL
AI_TIMEOUT
DASHSCOPE_API_KEY
DASHSCOPE_API_URL
DASHVECTOR_API_KEY
DASHVECTOR_ENDPOINT
CHUNK_STORE_PATH
MAX_RETRIES
RETRY_BASE_DELAY
RETRY_MAX_DELAY
LOG_LEVEL
LOG_FORMAT
CORE_SAAS_TELEGRAM_URL
CORE_LOGIN_TELEGRAM_URL
EXPOZY_ADMIN_LOGIN_URL
EXPOZY_STORE_DOMAIN
POSTGRES_USER
POSTGRES_PASSWORD
POSTGRES_DB
RABBITMQ_USER
RABBITMQ_PASS
```

### Deployment

Push to `main` triggers a deployment via SSH to the VPS:

```bash
git pull origin main
docker compose up -d --build
docker compose run --rm migrations
```

---

## Running Tests

### Install test dependencies

```bash
pip install pytest coverage ruff pyright
pip install -r requirements.txt
```

### Run unit tests

```bash
# All tests
pytest tests/ -v

# With coverage
coverage run -m pytest tests/ -v
coverage report

# HTML report
coverage html
open htmlcov/index.html
```

### Test structure

```
tests/
├── conftest.py                          # Shared fakes and stubs
├── service/
│   ├── test_business_context_extractor.py
│   ├── test_page_selector.py
│   ├── test_page_generator.py
│   ├── test_site_generator.py
│   ├── test_job_processor.py
│   └── test_worker.py
└── persistance/
    └── test_worker_persistance.py
```

All unit tests use fakes and stubs — no live API, database, or queue is required to run them.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | ✅ | PostgreSQL async connection string |
| `RABBITMQ_URL` | ✅ | RabbitMQ AMQP connection string |
| `JOB_QUEUE_NAME` | ✅ | Queue name for AI jobs |
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot token from @BotFather |
| `TELEGRAM_SECRET_TOKEN` | ✅ | Webhook authentication secret |
| `PREVIEW_BASE_URL` | ✅ | Public URL of the preview service |
| `AI_PROVIDER` | ✅ | `dashscope` |
| `AI_MODEL` | ✅ | e.g. `qwen3-coder-plus` |
| `AI_TIMEOUT` | ✅ | Seconds before AI call times out |
| `DASHSCOPE_API_KEY` | ✅ | Alibaba Cloud DashScope key |
| `DASHSCOPE_API_URL` | ✅ | DashScope endpoint URL |
| `DASHVECTOR_API_KEY` | ✅ | DashVector key for RAG |
| `DASHVECTOR_ENDPOINT` | ✅ | DashVector cluster endpoint |
| `CHUNK_STORE_PATH` | ✅ | Path to chunk_store.json |
| `MAX_RETRIES` | ❌ | Max job retry attempts (default: 5) |
| `RETRY_BASE_DELAY` | ❌ | Base backoff delay in seconds (default: 2) |
| `RETRY_MAX_DELAY` | ❌ | Max backoff delay in seconds (default: 300) |
| `LOG_LEVEL` | ❌ | `INFO` or `DEBUG` (default: INFO) |
| `LOG_FORMAT` | ❌ | `json` or `text` (default: json) |
| `CORE_SAAS_TELEGRAM_URL` | ✅ | Expozy Core saas_telegram endpoint |
| `CORE_LOGIN_TELEGRAM_URL` | ✅ | Expozy Core login_telegram endpoint |
| `EXPOZY_ADMIN_LOGIN_URL` | ✅ | Expozy admin login URL |
| `EXPOZY_STORE_DOMAIN` | ✅ | Store domain suffix (e.g. expozy.net) |

---

## Troubleshooting

**Services won't start**
```bash
docker compose ps
docker compose logs postgres
docker compose logs rabbitmq
```

**Jobs stuck in QUEUED**
```bash
docker compose ps worker
docker compose logs -f worker
```

**Preview returns 404**
```bash
docker compose exec worker ls /previews/
docker volume inspect expozy_previews_data
docker compose logs preview
```

**Webhook returns 401**

Verify `TELEGRAM_SECRET_TOKEN` in `.env` matches the token used in the `setWebhook` call.

**AI generation fails**

Check `DASHSCOPE_API_KEY` is valid and `AI_TIMEOUT` is high enough — Qwen generation can take up to 60 seconds for a full site.