# syntax=docker/dockerfile:1.4

FROM python:3.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# If you STILL have shared/ at repo root, keep this:
COPY shared/ ./shared/

# Always copy api/ because worker + preview live inside it now
COPY api/ ./api/

# =============================================================================
# API Service
# =============================================================================
FROM base AS api
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

# =============================================================================
# Worker Service
# =============================================================================
FROM base AS worker
CMD ["python", "-m", "api.orchestrator.worker.main"]

# =============================================================================
# Preview Service
# =============================================================================
FROM base AS preview
EXPOSE 8001
CMD ["uvicorn", "api.orchestrator.preview.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2"]

# =============================================================================
# Migrations
# =============================================================================
FROM base AS migrations
COPY migrations/ ./migrations/
COPY alembic.ini .
CMD ["alembic", "upgrade", "head"]
