# Environment Variables Reference

Complete list of environment variables for the AI Orchestrator.

## Required Variables

### Database (PostgreSQL)

| Variable | Description | Example |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string with asyncpg driver | `postgresql+asyncpg://user:pass@host:5432/db` |

### Message Queue (RabbitMQ)

| Variable | Description | Example |
|----------|-------------|---------|
| `RABBITMQ_URL` | RabbitMQ AMQP connection string | `amqp://user:pass@host:5672/` |

### Telegram Bot

| Variable | Description | Example |
|----------|-------------|---------|
| `TELEGRAM_SECRET_TOKEN` | Webhook secret token for X-Telegram-Bot-Api-Secret-Token header validation | `your-secret-token-here` |
| `TELEGRAM_BOT_TOKEN` | Bot API token from @BotFather (for sending messages) | `123456789:ABCdefGHIjklMNOpqrsTUVwxyz` |

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

### AI Provider

| Variable | Description | Default |
|----------|-------------|---------|
| `AI_PROVIDER` | Provider to use: `gemini`, `openai`, `mock` | `mock` |
| `AI_API_KEY` | API key for the AI provider | `""` |
| `AI_MODEL` | Model identifier | `gemini-2.0-flash` |
| `AI_TIMEOUT` | Request timeout in seconds | `120` |

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

## Environment File Example

Create a `.env` file in the project root:

```bash
# =============================================================================
# REQUIRED - Database
# =============================================================================
DATABASE_URL=postgresql+asyncpg://orchestrator:orchestrator_secret@localhost:5432/orchestrator

# =============================================================================
# REQUIRED - Message Queue
# =============================================================================
RABBITMQ_URL=amqp://orchestrator:orchestrator_secret@localhost:5672/

# =============================================================================
# REQUIRED - Telegram Webhook Authentication
# =============================================================================
# Generate with: openssl rand -hex 32
TELEGRAM_SECRET_TOKEN=your-webhook-secret-token-here

# =============================================================================
# REQUIRED - Telegram Bot (for sending "Working on it..." messages)
# =============================================================================
# Get from @BotFather on Telegram
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# =============================================================================
# OPTIONAL - AI Provider
# =============================================================================
AI_PROVIDER=gemini
AI_API_KEY=your-api-key-here
AI_MODEL=gemini-2.0-flash
AI_TIMEOUT=120

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

## Docker Compose Environment

When using Docker Compose, environment variables are set in `docker-compose.yml`:

```yaml
services:
  api:
    environment:
      - DATABASE_URL=postgresql+asyncpg://orchestrator:orchestrator_secret@postgres:5432/orchestrator
      - RABBITMQ_URL=amqp://orchestrator:orchestrator_secret@rabbitmq:5672/
      - TELEGRAM_SECRET_TOKEN=${TELEGRAM_SECRET_TOKEN}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
```

Variables with `${VAR}` syntax are read from the host's `.env` file or shell environment.

## Security Notes

1. **TELEGRAM_SECRET_TOKEN**: 
   - Generate a cryptographically secure random string
   - Use `openssl rand -hex 32` to generate
   - Must match what you set when configuring the webhook with Telegram API

2. **TELEGRAM_BOT_TOKEN**:
   - Keep secret - anyone with this token can control your bot
   - Never commit to version control
   - Get from @BotFather: https://t.me/BotFather

3. **AI_API_KEY**:
   - Keep secret
   - Consider using a secrets manager in production

4. **Database/RabbitMQ credentials**:
   - Use strong passwords in production
   - Consider using managed services with proper IAM
