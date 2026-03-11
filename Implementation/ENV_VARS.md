# Environment Variables Reference

Complete list of environment variables for the AI Orchestrator.

---

## Required Variables

### Database (PostgreSQL)

| Variable | Description | Example |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string with `asyncpg` driver | `postgresql+asyncpg://user:pass@host:5432/db` |

### Message Queue (RabbitMQ)

| Variable | Description | Example |
|----------|-------------|---------|
| `RABBITMQ_URL` | RabbitMQ AMQP connection string | `amqp://user:pass@host:5672/` |

### Telegram Bot

| Variable | Description | Example |
|----------|-------------|---------|
| `TELEGRAM_SECRET_TOKEN` | Webhook secret token for `X-Telegram-Bot-Api-Secret-Token` validation | `your-secret-token-here` |
| `TELEGRAM_BOT_TOKEN` | Bot API token from @BotFather (used to send messages) | `123456789:ABCdefGHIjklMNOpqrsTUVwxyz` |

### AI Provider (Vertex AI)

| Variable | Description | Example |
|----------|-------------|---------|
| `AI_PROVIDER` | AI provider (must be `vertex`) | `vertex` |
| `AI_MODEL` | Model identifier (Gemini model name on Vertex AI) | `gemini-2.5-pro` |
| `AI_TIMEOUT` | Request timeout in seconds | `120` |

### Vertex AI (Google Cloud)

| Variable | Description | Example |
|----------|-------------|---------|
| `VERTEX_PROJECT_ID` | Google Cloud project id | `leafy-acumen-483218-d3` |
| `VERTEX_REGION` | Vertex region | `europe-west1` |
| `VERTEX_SERVICE_ACCOUNT_JSON` | Service account JSON as a **single-line** string | `{"type":"service_account",...}` |

### Preview Links

| Variable | Description | Example |
|----------|-------------|---------|
| `PREVIEW_BASE_URL` | Base URL used to build preview links sent to Telegram | `http://localhost:8001` |

---

## Optional Variables

### Database Connection Pool

| Variable | Description | Default |
|----------|-------------|---------|
| `DB_POOL_SIZE` | Connection pool size | `10` |
| `DB_MAX_OVERFLOW` | Max overflow connections | `20` |

### RabbitMQ

| Variable | Description | Default |
|----------|-------------|---------|
| `JOB_QUEUE_NAME` | Queue name for job messages | `ai_jobs` |

### Worker Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `MAX_RETRIES` | Maximum retry attempts for failed jobs | `3` |
| `RETRY_BASE_DELAY` | Base delay for exponential backoff (seconds) | `5` |
| `RETRY_MAX_DELAY` | Maximum delay between retries (seconds) | `300` |

### Storage

| Variable | Description | Default |
|----------|-------------|---------|
| `PREVIEWS_PATH` | Path for preview bundles storage | `/previews` |

### Logging

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_LEVEL` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` | `INFO` |
| `LOG_FORMAT` | Log format: `json`, `console` | `json` |

---

## Environment File Example

Create a `.env` file in the project root:

```bash
# =============================================================================
# REQUIRED - Database
# =============================================================================
DATABASE_URL=postgresql+asyncpg://orchestrator:orchestrator_secret@postgres:5432/orchestrator

# =============================================================================
# REQUIRED - Message Queue
# =============================================================================
RABBITMQ_URL=amqp://orchestrator:orchestrator_secret@rabbitmq:5672/

# =============================================================================
# REQUIRED - Telegram Webhook Authentication
# =============================================================================
# Generate with: openssl rand -hex 32
TELEGRAM_SECRET_TOKEN=your-webhook-secret-token-here

# =============================================================================
# REQUIRED - Telegram Bot Token (for sending messages)
# =============================================================================
# Get from @BotFather on Telegram
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# =============================================================================
# REQUIRED - Preview Links (used in Telegram messages)
# =============================================================================
PREVIEW_BASE_URL=http://localhost:8001

# =============================================================================
# REQUIRED - AI Provider (Vertex AI)
# =============================================================================
AI_PROVIDER=vertex
AI_MODEL=gemini-2.5-pro
AI_TIMEOUT=120

# =============================================================================
# REQUIRED - Vertex AI (Google Cloud)
# =============================================================================
VERTEX_PROJECT_ID=your-gcp-project-id
VERTEX_REGION=europe-west1
# Service account JSON (single line - DO NOT add line breaks)
VERTEX_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"your-gcp-project-id","private_key_id":"...","private_key":"-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n","client_email":"...","client_id":"...","token_uri":"https://oauth2.googleapis.com/token","universe_domain":"googleapis.com"}

# =============================================================================
# OPTIONAL - Worker
# =============================================================================
MAX_RETRIES=3
RETRY_BASE_DELAY=5
RETRY_MAX_DELAY=300

# =============================================================================
# OPTIONAL - Storage
# =============================================================================
PREVIEWS_PATH=/previews

# =============================================================================
# OPTIONAL - Logging
# =============================================================================
LOG_LEVEL=INFO
LOG_FORMAT=json
```

---

## Docker Compose Environment

Recommended: load variables from the project `.env` file using `env_file`:

```yaml
services:
  api:
    env_file:
      - .env

  worker:
    env_file:
      - .env

  preview:
    env_file:
      - .env
```

If you use explicit `environment:` entries instead, make sure they include the required values (especially `AI_PROVIDER`, the Vertex variables, and Telegram tokens).

---

## Security Notes

1. **TELEGRAM_SECRET_TOKEN**
   - Generate a secure random string
   - Must match what you set when configuring the Telegram webhook

2. **TELEGRAM_BOT_TOKEN**
   - Keep secret (anyone with it can control your bot)
   - Never commit it to version control

3. **VERTEX_SERVICE_ACCOUNT_JSON**
   - Treat as a secret (it contains a private key)
   - Never commit it to version control
   - Keep it as a **single line** in `.env` (escaped `\n` inside JSON is fine)
   - For production, use a secrets manager (Docker secrets / cloud secret manager)

4. **Database/RabbitMQ credentials**
   - Use strong passwords in production
   - Avoid exposing ports publicly unless needed
