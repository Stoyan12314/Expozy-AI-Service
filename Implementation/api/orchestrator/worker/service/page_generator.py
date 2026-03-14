"""
Steps 3 & 3b — Page Generator
Handles single-page HTML generation via RAG + AI, with retry loop and error feedback injection.
"""

import asyncio
import json
import re
from typing import Dict, List, Optional

from api.orchestrator.ai.providers.providers.base import PageConfig, GenerationResult
from shared.utils import get_logger

logger = get_logger(__name__)

MAX_PAGE_RETRIES = 3
RETRY_DELAY_SECONDS = 0.5
DEFAULT_MAX_TOKENS = 8192
DEFAULT_TEMPERATURE = 0.5


class PageGenerator:
    def __init__(self, provider, rag, catalog):
        self.provider = provider
        self.rag = rag
        self.catalog = catalog

    # ── Validator ────────────────────────────────────────────────────────────

    def _wrap_validator(self, data, page_type, schema_path=""):
        try:
            from api.orchestrator.ai.providers.validator import validate_template
            return validate_template(data=data, page_type=page_type)
        except ImportError:
            logger.warning("Validator not available, skipping validation")
            return None
        except Exception as e:
            logger.warning("Validator error", error=str(e))
            return None

    # ── Error hints ──────────────────────────────────────────────────────────

    def _build_error_hints(self, errors: List[str], hints_map: Dict[str, str]) -> str:
        if not hints_map:
            return ""

        code_pattern = re.compile(r"^([A-Z]{2,4}-\d{3})")
        seen_codes: set = set()
        matched_hints = []

        for error in errors:
            m = code_pattern.match(error.strip())
            if not m:
                continue
            code = m.group(1)
            if code in seen_codes:
                continue
            seen_codes.add(code)
            hint_text = hints_map.get(code)
            if hint_text:
                matched_hints.append(f"{code}: {hint_text}")

        if not matched_hints:
            return ""
        return "\n\nFIX INSTRUCTIONS:\n" + "\n".join(f"  • {h}" for h in matched_hints)

    # ── Single generation call ───────────────────────────────────────────────

    async def generate(
        self,
        prompt: str,
        page_type: str,
        business_context: dict,
        lang: str,
        selected_pages: Optional[List[str]] = None,
    ) -> GenerationResult:
        shared_rules = self.catalog.shared_rules_prompt()

        if self.catalog.is_global_type(page_type):
            rag_context = await self.rag.global_type_context(
                global_type=page_type,
                selected_pages=selected_pages or [],
            )

            nav_links = []
            for pid in (selected_pages or []):
                if not self.catalog.is_global_type(pid):
                    try:
                        route = self.catalog.route(pid)
                        nav_links.append(f"  {route} → {pid}")
                    except KeyError:
                        nav_links.append(f"  /{pid} → {pid}")

            output_file = self.catalog.output_file(page_type)

            system_prompt = f"""{shared_rules}

=== BUSINESS CONTEXT ===
{json.dumps(business_context, indent=2, ensure_ascii=False)}

=== YOUR TASK: Generate the {page_type} ===
Type: {page_type} (global — appears on every page)
Output file: {output_file}

=== SELECTED PAGES (generate nav links for these ONLY) ===
{chr(10).join(nav_links)}

IMPORTANT: Generate <a> links ONLY for pages listed above. Use the route as href.

{rag_context}

Generate the complete HTML for the {page_type}.
Do NOT include <!DOCTYPE>, <html>, <head>, or <body> tags — only the {page_type} HTML.
Write all content in {"Bulgarian" if lang == "bg" else lang}.
Output ONLY the HTML, nothing else."""

        else:
            rag_context = await self.rag.page_generation_context(
                page_type=page_type,
                business_context=business_context,
                prompt=prompt,
            )

            output_file = self.catalog.output_file(page_type)

            system_prompt = f"""{shared_rules}

=== BUSINESS CONTEXT ===
{json.dumps(business_context, indent=2, ensure_ascii=False)}

=== YOUR TASK: Generate {output_file} ===
Page type: {page_type}

Below is the CATALOG CONTEXT retrieved for this page.
It contains: component definitions, page structure, AI fill instructions,
runtime interactions, endpoints, validation rules, and global config.

Use ONLY what is described below. Do NOT invent components or endpoints.

{rag_context}

Generate the complete HTML for the {page_type} page.
Do NOT include <!DOCTYPE>, <html>, <head>, or <body> tags — only the page content HTML.
Write all content in {"Bulgarian" if lang == "bg" else lang}.
Output ONLY the HTML, nothing else."""

        page_config = PageConfig(
            system_prompt=system_prompt,
            max_tokens=DEFAULT_MAX_TOKENS,
            temperature=DEFAULT_TEMPERATURE,
            validate_fn=self._wrap_validator,
            expect_html=True,
        )

        return await self.provider.generate(
            prompt=prompt,
            page_type=page_type,
            page_config=page_config,
            lang=lang,
        )

    # ── Retry loop ───────────────────────────────────────────────────────────

    async def generate_with_retries(
        self,
        prompt: str,
        page_type: str,
        business_context: dict,
        lang: str,
        selected_pages: Optional[List[str]] = None,
    ) -> GenerationResult:
        last_result = None
        errors_from_previous = []
        previous_html = None
        hints_map = self.catalog.error_hints()

        for attempt in range(1, MAX_PAGE_RETRIES + 1):
            logger.info("Generating page", page_type=page_type, attempt=attempt)

            effective_prompt = prompt

            if errors_from_previous:
                error_lines = "\n".join(
                    f"  {i+1}. {e}" for i, e in enumerate(errors_from_previous[:15])
                )
                hints = self._build_error_hints(errors_from_previous, hints_map)

                if attempt <= 3 and previous_html and len(previous_html) < 50000:
                    effective_prompt = (
                        f"{prompt}\n\n"
                        f"══ VALIDATION FAILED (attempt {attempt - 1}/{MAX_PAGE_RETRIES}) ══\n"
                        f"Your previous HTML was REJECTED with {len(errors_from_previous)} errors:\n"
                        f"{error_lines}"
                        f"{hints}\n\n"
                        f"══ REJECTED HTML (fix this) ══\n"
                        f"{previous_html}\n"
                        f"══ END REJECTED HTML ══\n\n"
                        f"Fix ALL errors listed above. Output the COMPLETE corrected HTML. "
                        f"Keep everything that was correct — only fix the problems."
                    )
                else:
                    effective_prompt = (
                        f"{prompt}\n\n"
                        f"══ CRITICAL: AVOID THESE ERRORS ══\n"
                        f"Previous {attempt - 1} attempts ALL failed validation. "
                        f"You MUST avoid these errors:\n"
                        f"{error_lines}"
                        f"{hints}\n\n"
                        f"Generate a COMPLETE, VALID HTML template from scratch. "
                        f"Double-check: no <script> tags, all tags closed, "
                        f"all Alpine directives from the allowed list."
                    )

            result = await self.generate(
                prompt=effective_prompt,
                page_type=page_type,
                business_context=business_context,
                lang=lang,
                selected_pages=selected_pages,
            )
            last_result = result

            if result.success:
                logger.info(
                    "Page generated successfully",
                    page_type=page_type,
                    attempt=attempt,
                    latency_ms=result.latency_ms,
                )
                return result

            errors_from_previous = result.all_errors()
            previous_html = result.template if isinstance(result.template, str) else None

            logger.warning(
                "Page generation failed",
                page_type=page_type,
                attempt=attempt,
                errors=errors_from_previous[:3],
            )

            if not result.retryable:
                return result

            if attempt < MAX_PAGE_RETRIES:
                await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)

        logger.error("Page failed after all retries", page_type=page_type)
        return last_result