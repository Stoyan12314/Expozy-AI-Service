"""
Step 1 — Business Context Extractor
Extracts structured business information from a raw user prompt using AI.
"""

import json

from api.orchestrator.ai.providers.providers.base import PageConfig, GenerationResult
from shared.utils import get_logger

logger = get_logger(__name__)

CONTEXT_EXTRACTION_MAX_TOKENS = 2048


class BusinessContextExtractor:
    def __init__(self, provider, rag, catalog):
        self.provider = provider
        self.rag = rag
        self.catalog = catalog

    async def extract(self, prompt: str, lang: str) -> dict | None:
        schema_context = await self.rag.business_context_schema_context()

        system_prompt = f"""You are a business information extractor for website generation.

Extract structured data from the user's description. Return ONLY valid JSON, no markdown.

Here is the schema of fields to extract:

{schema_context}

Additional rules:
- company_name: Extract or derive from business type.
- business_type: Categorize (e.g. "Dental clinic", "Restaurant", "Law firm").
- services: List specific services mentioned.
- primary_language: "{lang}"
- Output language for all text: {"Bulgarian" if lang == "bg" else lang}

Return ONLY the JSON object."""

        config = PageConfig(
            system_prompt=system_prompt,
            max_tokens=CONTEXT_EXTRACTION_MAX_TOKENS,
            temperature=0.3,
            response_schema=self.catalog.business_context_response_schema(),
        )

        result: GenerationResult = await self.provider.generate(
            prompt=prompt,
            page_type="context_extraction",
            page_config=config,
            lang=lang,
        )

        if result.success and result.template:
            return result.template

        if result.raw_response:
            try:
                return json.loads(result.raw_response)
            except json.JSONDecodeError:
                pass

        logger.error("Context extraction failed", error=result.error)
        return None