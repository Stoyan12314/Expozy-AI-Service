"""
Shared fixtures and stubs for EXPOZY AI Orchestrator worker tests.

All external boundaries are replaced with fakes or stubs as described
in the test strategy (Section 4.1):
  - LLM provider  → ProviderStub  (returns canned JSON/HTML)
  - RabbitMQ      → InProcessQueueFake (synchronous enqueue/dequeue)
  - Expozy API    → ExpozyPublisherStub (returns synthetic page IDs)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.orchestrator.ai.providers.providers.base import GenerationResult


# ── Provider stub ─────────────────────────────────────────────────────────────

class ProviderStub:
    """
    Stub for the AI provider (F.REQ6, F.REQ7).
    Returns canned GenerationResult objects. Call history is recorded so tests
    can assert that no provider call occurs inside the webhook path (NF.REQ1).
    """

    def __init__(self, results: dict[str, GenerationResult] | None = None):
        self._results: dict[str, GenerationResult] = results or {}
        self.calls: list[dict] = []

    def set_result(self, page_type: str, result: GenerationResult) -> None:
        self._results[page_type] = result

    async def generate(self, prompt: str, page_type: str, page_config, lang: str = "bg") -> GenerationResult:
        self.calls.append({"prompt": prompt, "page_type": page_type, "lang": lang})
        if page_type in self._results:
            return self._results[page_type]
        return GenerationResult(success=True, template="<section>stub</section>", page_type=page_type, latency_ms=10)


def make_success_result(page_type: str, template: Any = None) -> GenerationResult:
    return GenerationResult(success=True, template=template or "<section>stub</section>", page_type=page_type, latency_ms=10)


def make_failure_result(page_type: str, errors: list[str], retryable: bool = True) -> GenerationResult:
    return GenerationResult(success=False, template=None, page_type=page_type, latency_ms=10,  error=errors[0] if errors else None, retryable=retryable)


# ── RAG stub ──────────────────────────────────────────────────────────────────

class RAGStub:
    async def business_context_schema_context(self) -> str: return "{}"
    async def page_selection_context(self, business_context: dict) -> str: return ""
    async def global_type_context(self, global_type: str, selected_pages: list) -> str: return ""
    async def page_generation_context(self, page_type: str, business_context: dict, prompt: str) -> str: return ""


# ── Catalog stub ──────────────────────────────────────────────────────────────

class CatalogStub:
    """
    Fake for the component/page catalog.
    Behaves consistently with inputs rather than returning fixed values (Fowler, 2006).
    """
    _PAGE_TYPES = ["page_main", "page_a", "page_b", "page_global_header", "page_global_footer"]
    _GLOBAL_TYPES = {"page_global_header", "page_global_footer"}
    _REQUIRED = {"page_main"}
    _DEPS: dict[str, list[str]] = {}

    def all_page_type_ids(self) -> list[str]: return list(self._PAGE_TYPES)
    def global_type_ids(self) -> list[str]: return list(self._GLOBAL_TYPES)
    def is_global_type(self, page_type: str) -> bool: return page_type in self._GLOBAL_TYPES
    def required_page_ids(self) -> list[str]: return list(self._REQUIRED)
    def generation_order(self) -> list[str]: return self._PAGE_TYPES
    def requires(self, page_id: str) -> list[str]: return self._DEPS.get(page_id, [])
    def route(self, page_id: str) -> str: return f"/{page_id}"
    def output_file(self, page_type: str) -> str: return f"{page_type}.html"
    def shared_rules_prompt(self) -> str: return "# Shared rules (stub)"
    def error_hints(self) -> dict[str, str]: return {"STR-001": "Use only allowed tags."}
    def business_context_response_schema(self) -> dict: return {}
    def page_selection_response_schema(self) -> dict: return {}
# ── In-process queue fake ─────────────────────────────────────────────────────

@dataclass
class QueueMessage:
    job_id: uuid.UUID
    attempt: int


class InProcessQueueFake:
    """
    Fake for RabbitMQ (F.REQ4, F.REQ15).
    Supports synchronous enqueue/dequeue and tracks published messages so
    duplicate-prevention behavior can be asserted in isolation.
    """

    def __init__(self):
        self._queue: list[QueueMessage] = []
        self.published: list[dict] = []

    async def publish_job(self, job_id: uuid.UUID, attempt: int = 1) -> None:
        self._queue.append(QueueMessage(job_id=job_id, attempt=attempt))
        self.published.append({"job_id": job_id, "attempt": attempt})

    async def publish_job_delayed(self, job_id: uuid.UUID, attempt: int, delay: float) -> None:
        self._queue.append(QueueMessage(job_id=job_id, attempt=attempt))
        self.published.append({"job_id": job_id, "attempt": attempt, "delay": delay})

    async def consume(self, handler: Callable) -> None:
        while self._queue:
            await handler(self._queue.pop(0))

    def depth(self) -> int:
        return len(self._queue)


# ── Expozy publisher stub ─────────────────────────────────────────────────────

class ExpozyPublisherStub:
    def __init__(self, fail: bool = False):
        self._fail = fail
        self.pushed: list[tuple[str, str]] = []

    async def push_all(self, pages: list[tuple[str, str]]) -> list[dict]:
        if self._fail:
            raise RuntimeError("Simulated upload failure")
        result = []
        for title, html in pages:
            self.pushed.append((title, html))
            result.append({"title": title, "url": f"https://preview.example.com/{title}"})
        return result


# ── Pytest fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def provider(): return ProviderStub()

@pytest.fixture
def rag(): return RAGStub()

@pytest.fixture
def catalog(): return CatalogStub()

@pytest.fixture
def queue(): return InProcessQueueFake()

@pytest.fixture
def publisher(): return ExpozyPublisherStub()

@pytest.fixture
def failing_publisher(): return ExpozyPublisherStub(fail=True)

@pytest.fixture
def sample_job_id(): return uuid.uuid4()

@pytest.fixture
def sample_business_context():
    return {
        "company_name": "Test Café",
        "business_type": "Restaurant",
        "services": ["coffee", "pastries"],
        "primary_language": "bg",
    }