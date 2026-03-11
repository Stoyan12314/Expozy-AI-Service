# AI Orchestrator

Telegram bot-driven AI template generation system. Users send prompts via Telegram and receive generated preview websites.

## Architecture Overview

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
       │                    │
       │                    ▼
       │            ┌──────────────┐
       │            │ AI Provider  │
       │            │(Gemini/GPT)  │
       │            └──────────────┘
       │
       ▼
┌──────────────┐
│ /previews/   │  ◀── Shared Volume (previews_data)
│   (files)    │
└──────────────┘
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| **api** | 8000 | Telegram webhook handler |
| **worker** | - | Background job processor |
| **preview** | 8001 | Secure static file server |
| **postgres** | 5432 | PostgreSQL database |
| **rabbitmq** | 5672, 15672 | Message queue |

---

## 🚀 Local Development Setup

### Prerequisites

- Docker & Docker Compose v2+
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- (Optional) AI API key (Gemini or OpenAI)

### Step 1: Clone and Configure

```bash
# Clone repository
git clone <repo-url>
cd ai-orchestrator

# Copy environment template
cp .env.example .env
```

### Step 2: Edit Environment Variables

`.env` with values:

```bash
# Required - Get from @BotFather
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# Required - Generate with: openssl rand -hex 32
TELEGRAM_SECRET_TOKEN=your-random-secret-here

# Required - Preview URL (localhost for local dev)
PREVIEW_BASE_URL=http://localhost:8001

# Optional - AI Provider (defaults to mock)
AI_PROVIDER=gemini
AI_API_KEY=api-key
```

### Step 3: Start Services

```bash
# Start all services
docker-compose up -d

# Wait for services to be healthy
docker-compose ps

# Run database migrations
docker-compose run --rm migrations

# View logs
docker-compose logs -f
```

### Step 4: Verify Services

```bash
# Check API health
curl http://localhost:8000/health

# Check Preview health
curl http://localhost:8001/health

# RabbitMQ Management UI
open http://localhost:15672
# Login: orchestrator / orchestrator_secret
```

### Step 5: Test Webhook (Local)

For local testing without Telegram:

```bash
curl -X POST http://localhost:8000/telegram/webhook \
  -H "Content-Type: application/json" \
  -H "X-Telegram-Bot-Api-Secret-Token: your-random-secret-here" \
  -d '{
    "update_id": 123456789,
    "message": {
      "message_id": 1,
      "date": 1234567890,
      "chat": {"id": 12345, "type": "private"},
      "from": {"id": 12345, "is_bot": false, "first_name": "Test"},
      "text": "Create a landing page for a coffee shop"
    }
  }'
```

### Step 6: Configure Telegram Webhook (with ngrok)

For real Telegram integration during development:

```bash
# Install and start ngrok
ngrok http 8000

# Set webhook (replace with your ngrok URL and bot token)
curl -X POST "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-ngrok-url.ngrok.io/telegram/webhook",
    "secret_token": "your-random-secret-here"
  }'
```

### Useful Commands

```bash
# Stop all services
docker-compose down

# Stop and remove volumes (reset data)
docker-compose down -v

# Rebuild after code changes
docker-compose build
docker-compose up -d

# View specific service logs
docker-compose logs -f worker

# Scale workers
docker-compose up -d --scale worker=3

# Access PostgreSQL
docker-compose exec postgres psql -U orchestrator -d orchestrator

# List preview bundles
docker-compose exec worker ls -la /previews/
```

---

## 🏭 Production Deployment

### Infrastructure Requirements

- Docker host or Kubernetes cluster
- PostgreSQL (managed recommended: RDS, Cloud SQL)
- RabbitMQ (managed recommended: CloudAMQP, Amazon MQ)
- Reverse proxy with TLS (nginx, Traefik, or cloud load balancer)
- **Two separate domains/subdomains** (see Security section)

### Domain Configuration

⚠️ **CRITICAL SECURITY REQUIREMENT**

The preview service **MUST** run on a separate domain/subdomain:

```
✅ SECURE Configuration:
   API:     https://api.example.com
   Preview: https://preview.example.com

✅ SECURE Configuration:
   API:     https://app.example.com
   Preview: https://previews.example.com

❌ INSECURE (same domain):
   API:     https://example.com/api
   Preview: https://example.com/preview
```

**Why?** The preview service serves AI-generated HTML which is untrusted. Same-domain deployment allows:
- Cookie theft (session hijacking)
- Cross-site request forgery
- Access to localStorage/sessionStorage

### TLS Termination with Nginx

Create `nginx.conf`:

```nginx
# API Service (api.example.com)
server {
    listen 443 ssl http2;
    server_name api.example.com;

    ssl_certificate /etc/ssl/certs/api.example.com.crt;
    ssl_certificate_key /etc/ssl/private/api.example.com.key;
    
    # Modern TLS configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
    ssl_prefer_server_ciphers off;

    location / {
        proxy_pass http://api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Webhook needs fast response
        proxy_read_timeout 30s;
    }

    location /health {
        proxy_pass http://api:8000/health;
    }
}

# Preview Service (preview.example.com) - SEPARATE DOMAIN
server {
    listen 443 ssl http2;
    server_name preview.example.com;

    ssl_certificate /etc/ssl/certs/preview.example.com.crt;
    ssl_certificate_key /etc/ssl/private/preview.example.com.key;
    
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
    ssl_prefer_server_ciphers off;

    # IMPORTANT: No cookies on preview domain
    add_header Set-Cookie "" always;
    
    # Additional security headers (preview service adds its own CSP)
    add_header X-Robots-Tag "noindex, nofollow" always;

    location / {
        proxy_pass http://preview:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name api.example.com preview.example.com;
    return 301 https://$server_name$request_uri;
}
```

### Production docker-compose.override.yml

```yaml
version: "3.8"

services:
  api:
    # Don't expose ports directly - use reverse proxy
    ports: []
    environment:
      - LOG_FORMAT=json
    deploy:
      replicas: 2
      resources:
        limits:
          cpus: '1'
          memory: 512M

  worker:
    deploy:
      replicas: 3
      resources:
        limits:
          cpus: '2'
          memory: 1G

  preview:
    ports: []
    deploy:
      replicas: 2
      resources:
        limits:
          cpus: '0.5'
          memory: 256M

  # Use external managed services
  postgres:
    # Comment out for managed PostgreSQL
    profiles: ["dev-only"]

  rabbitmq:
    # Comment out for managed RabbitMQ
    profiles: ["dev-only"]
```

### Production Environment Variables

```bash
# Use managed database
DATABASE_URL=postgresql+asyncpg://user:pass@rds-instance.region.rds.amazonaws.com:5432/orchestrator

# Use managed message queue
RABBITMQ_URL=amqps://user:pass@rabbitmq.cloudamqp.com/vhost

# Production preview URL
PREVIEW_BASE_URL=https://preview.example.com

# Production AI provider
AI_PROVIDER=gemini
AI_API_KEY=<from-secret-manager>

# Production logging
LOG_LEVEL=INFO
LOG_FORMAT=json
```

### Telegram Webhook Setup (Production)

```bash
curl -X POST "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://api.example.com/telegram/webhook",
    "secret_token": "<TELEGRAM_SECRET_TOKEN>",
    "allowed_updates": ["message"],
    "drop_pending_updates": true
  }'

# Verify webhook
curl "https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo"
```

### Health Monitoring

```bash
# API health
curl https://api.example.com/health

# Preview health  
curl https://preview.example.com/health
```

### Security Checklist

- [ ] Preview service on separate domain/subdomain
- [ ] TLS enabled on all public endpoints
- [ ] Database credentials in secret manager
- [ ] API keys in secret manager
- [ ] RabbitMQ management UI not publicly accessible
- [ ] PostgreSQL not publicly accessible
- [ ] Webhook secret token is cryptographically random
- [ ] Log aggregation configured
- [ ] Alerting on health check failures

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | ✅ | - | PostgreSQL connection string |
| `RABBITMQ_URL` | ✅ | - | RabbitMQ AMQP connection string |
| `TELEGRAM_BOT_TOKEN` | ✅ | - | Bot token from @BotFather |
| `TELEGRAM_SECRET_TOKEN` | ✅ | - | Webhook authentication secret |
| `PREVIEW_BASE_URL` | ✅ | - | Public URL for preview service |
| `AI_PROVIDER` | ❌ | `mock` | `gemini`, `openai`, or `mock` |
| `AI_API_KEY` | ❌ | - | API key for AI provider |
| `AI_MODEL` | ❌ | `gemini-2.0-flash` | Model identifier |
| `MAX_RETRIES` | ❌ | `5` | Max job retry attempts |
| `LOG_LEVEL` | ❌ | `INFO` | Logging level |

---

## Troubleshooting

### Services won't start

```bash
# Check service status
docker-compose ps

# Check logs for errors
docker-compose logs postgres
docker-compose logs rabbitmq
```

### Jobs stuck in "queued"

1. Check worker is running: `docker-compose ps worker`
2. Check worker logs: `docker-compose logs -f worker`
3. Verify RabbitMQ connection in worker logs

### Preview returns 404

1. Check bundle exists: `docker-compose exec worker ls /previews/`
2. Verify shared volume mount: `docker volume inspect previews_data`
3. Check preview service logs: `docker-compose logs preview`

### Webhook returns 401

1. Verify `TELEGRAM_SECRET_TOKEN` matches in `.env`
2. Verify same token was used in `setWebhook` call

---

## Testing

### Prerequisites

```bash
# Install dev dependencies
pip install -e ".[dev]"
```

### Running Unit Tests

```bash
# Run all unit tests
pytest tests/test_webhook.py tests/test_worker.py -v

# Run with coverage
pytest tests/test_webhook.py tests/test_worker.py --cov=shared --cov=api --cov=worker -v

# Run specific test
pytest tests/test_webhook.py::TestWebhookDeduplication::test_duplicate_update_does_not_create_job -v

# Run excluding slow tests
pytest -m "not slow" -v
```

### Test Structure

```
tests/
├── conftest.py          # Fixtures (db, mocks, factories)
├── test_webhook.py      # Webhook deduplication tests
├── test_worker.py       # Worker completion tests
└── test_integration.py  # Full end-to-end tests
```

### Key Unit Tests

**Webhook Deduplication (`test_webhook.py`):**
- `test_first_update_creates_job` - New update_id creates job
- `test_duplicate_update_does_not_create_job` - Same update_id blocked
- `test_different_update_ids_create_separate_jobs` - Unique IDs = unique jobs
- `test_webhook_returns_401_on_invalid_token` - Auth validation

**Worker Completion (`test_worker.py`):**
- `test_job_marked_completed_on_success` - Status transitions
- `test_bundle_directory_created` - Filesystem writes
- `test_preview_url_format` - URL generation
- `test_script_tags_removed` - HTML sanitization

### Running Integration Tests

Integration tests require running services:

```bash
# Step 1: Start services
docker-compose up -d

# Step 2: Run migrations
docker-compose run --rm migrations

# Step 3: Wait for services to be healthy
docker-compose ps

# Step 4: Run integration tests
RUN_INTEGRATION_TESTS=true pytest tests/test_integration.py -v

# Optional: Run full flow test (may take 60s with real AI)
RUN_INTEGRATION_TESTS=true pytest tests/test_integration.py::TestIntegrationWebhookToPreview::test_full_flow_webhook_to_preview -v
```

### Integration Test Configuration

```bash
# Override test URLs if needed
export API_URL=http://localhost:8000
export PREVIEW_URL=http://localhost:8001
export TELEGRAM_SECRET_TOKEN=your-test-secret

RUN_INTEGRATION_TESTS=true pytest tests/test_integration.py -v
```

### Coverage Report

```bash
# Generate HTML coverage report
pytest --cov=shared --cov=api --cov=worker --cov-report=html -v

# View report
open htmlcov/index.html
```

---

## License

MIT
