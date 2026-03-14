"""
Step 2 — Page Selector
Uses AI to decide which pages the business needs, then validates and resolves dependencies.
"""

import json
from typing import List

from api.orchestrator.ai.providers.providers.base import PageConfig, GenerationResult
from shared.utils import get_logger

logger = get_logger(__name__)

PAGE_SELECTION_MAX_TOKENS = 1024


class PageSelector:
    def __init__(self, provider, rag, catalog):
        self.provider = provider
        self.rag = rag
        self.catalog = catalog

    async def select(self, prompt: str, business_context: dict, lang: str) -> List[str]:
        pages_context = await self.rag.page_selection_context(business_context)

        system_prompt = f"""You are the page selection engine for an EXPOZY website generator.

Decide which pages this business needs based on the context below.

{pages_context}

BUSINESS CONTEXT:
{json.dumps(business_context, indent=2, ensure_ascii=False)}

USER PROMPT:
{prompt}

RULES:
- Return a JSON object with a "pages" array of page type IDs.
- Read each page's selection_guidance and decide based on business needs.
- ONLY include pages the user actually needs. Less is more.
- If the user asks for a specific set of pages, generate ONLY those.
- Legal pages (cookie_policy, gdpr_policy, terms_conditions) only if the business sells online.
- blog_listing + blog_post only if the user mentions blog/news/articles.
- userpage only if the business has e-commerce or user accounts.
- homepage is always included unless explicitly excluded.
- header and footer are handled separately — do NOT include them.

Return the JSON object."""

        config = PageConfig(
            system_prompt=system_prompt,
            max_tokens=PAGE_SELECTION_MAX_TOKENS,
            temperature=0.2,
            response_schema=self.catalog.page_selection_response_schema(),
        )

        result: GenerationResult = await self.provider.generate(
            prompt=prompt,
            page_type="page_selection",
            page_config=config,
            lang=lang,
        )

        selected = set()

        if result.success and result.template:
            if isinstance(result.template, list):
                selected.update(result.template)
            elif isinstance(result.template, dict) and "pages" in result.template:
                selected.update(result.template["pages"])
        elif result.raw_response:
            try:
                parsed = json.loads(result.raw_response)
                if isinstance(parsed, list):
                    selected.update(parsed)
            except json.JSONDecodeError:
                logger.warning("Page selection parse failed, using defaults")

        # Always include global types (header/footer)
        selected.update(self.catalog.global_type_ids())

        # Remove unknown page types
        valid = set(self.catalog.all_page_type_ids())
        invalid = selected - valid
        if invalid:
            logger.warning("Unknown page types removed", invalid=invalid)
        selected = selected & valid

        # Resolve dependencies
        for page_id in list(selected):
            selected.update(self.catalog.requires(page_id))

        return sorted(selected)