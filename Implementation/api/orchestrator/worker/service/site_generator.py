"""
Step 4 — Site Generator
Orchestrates the full site generation pipeline:
  - Delegates to BusinessContextExtractor, PageSelector, and PageGenerator
  - Runs content pages and global pages in parallel via asyncio.gather
"""

import asyncio
from typing import List

from api.orchestrator.ai.providers.providers.registry import get_provider
from api.orchestrator.ai.providers.providers.rag_context import RAGContextBuilder
from api.orchestrator.ai.providers.catalog_loader import get_catalog
from api.orchestrator.ai.providers.providers.base import GenerationResult

from .business_context_extractor import BusinessContextExtractor
from .page_selector import PageSelector
from .page_generator import PageGenerator

from shared.utils import get_logger

logger = get_logger(__name__)


class SiteGenerator:
    def __init__(self):
        self.provider = get_provider()
        self.rag = RAGContextBuilder()
        self.catalog = get_catalog()

        self.extractor = BusinessContextExtractor(self.provider, self.rag, self.catalog)
        self.selector = PageSelector(self.provider, self.rag, self.catalog)
        self.page_generator = PageGenerator(self.provider, self.rag, self.catalog)

    # ── Result collector ─────────────────────────────────────────────────────

    def _collect_result(
        self,
        page_type: str,
        result,
        pages: dict,
        all_errors: List[str],
    ) -> bool:
        """Returns True if the failure is retryable."""
        if isinstance(result, Exception):
            pages[page_type] = None
            all_errors.append(f"[{page_type}] {result}")
            return True

        if result.success:
            pages[page_type] = result.template
        else:
            pages[page_type] = None
            all_errors.extend([f"[{page_type}] {e}" for e in result.all_errors()])

        return result.retryable if not result.success else False

    # ── Main pipeline ─────────────────────────────────────────────────────────

    async def generate(self, prompt: str, lang: str = "bg") -> dict:
        all_errors = []
        total_latency = 0
        results = {}

        # Step 1: Business context
        logger.info("Step 1: extracting business context")
        business_context = await self.extractor.extract(prompt, lang)

        if not business_context:
            return {
                "success": False,
                "pages": {},
                "errors": ["Failed to extract business context from prompt"],
                "retryable": True,
                "results": {},
                "total_latency_ms": 0,
                "business_context": None,
                "selected_pages": [],
            }

        logger.info(
            "Business context extracted",
            company=business_context.get("company_name"),
            type=business_context.get("business_type"),
        )

        # Step 2: Page selection
        logger.info("Step 2: selecting pages")
        selected_pages = await self.selector.select(prompt, business_context, lang)
        logger.info("Pages selected", pages=selected_pages)

        # Split into content vs global (header/footer)
        generation_order = self.catalog.generation_order()
        content_pages = [
            p for p in generation_order
            if p in selected_pages and not self.catalog.is_global_type(p)
        ]
        global_pages = [
            p for p in generation_order
            if p in selected_pages and self.catalog.is_global_type(p)
        ]

        for p in selected_pages:
            if p not in content_pages and p not in global_pages:
                (global_pages if self.catalog.is_global_type(p) else content_pages).append(p)

        pages = {}
        retryable = False

        # Step 3: Content pages in parallel
        logger.info("Step 3: generating content pages in parallel", count=len(content_pages))

        content_tasks = [
            self.page_generator.generate_with_retries(
                prompt=prompt,
                page_type=pt,
                business_context=business_context,
                lang=lang,
            )
            for pt in content_pages
        ]
        content_results = await asyncio.gather(*content_tasks, return_exceptions=True)

        for pt, result in zip(content_pages, content_results):
            results[pt] = result
            if not isinstance(result, Exception):
                total_latency += result.latency_ms
            if self._collect_result(pt, result, pages, all_errors):
                retryable = True

        # Step 4: Global pages in parallel (need nav links from selected_pages)
        logger.info("Step 4: generating global pages in parallel", count=len(global_pages))

        global_tasks = [
            self.page_generator.generate_with_retries(
                prompt=prompt,
                page_type=pt,
                business_context=business_context,
                lang=lang,
                selected_pages=selected_pages,
            )
            for pt in global_pages
        ]
        global_results = await asyncio.gather(*global_tasks, return_exceptions=True)

        for pt, result in zip(global_pages, global_results):
            results[pt] = result
            if not isinstance(result, Exception):
                total_latency += result.latency_ms
            if self._collect_result(pt, result, pages, all_errors):
                retryable = True

        required = set(self.catalog.required_page_ids()) | set(self.catalog.global_type_ids())
        success = all(pages.get(p) is not None for p in required if p in selected_pages)

        return {
            "success": success,
            "pages": pages,
            "errors": all_errors,
            "retryable": retryable,
            "results": results,
            "total_latency_ms": total_latency,
            "business_context": business_context,
            "selected_pages": selected_pages,
        }