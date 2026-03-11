"""
Worker service for EXPOZY site generation.

How it works:
  1. Get a job from RabbitMQ
  2. Extract business info from the user prompt using AI
  3. Let AI pick which pages to generate
  4. Generate all content pages IN PARALLEL using RAG + AI (with retry + error feedback)
  5. Generate header + footer (after content pages, needs nav links)
  6. Wrap pages with header and footer
  7. Save everything and notify the user on Telegram
  8. Push pages to their Expozy store
"""

import asyncio
import json
import re
import signal
import sys
from typing import Any, Dict, List, Optional
from uuid import UUID

from shared.config import get_settings
from shared.utils import setup_logging, get_logger

from api.orchestrator.db.models import JobStatus, AttemptOutcome
from api.orchestrator.db.session import get_db_session
from api.orchestrator.ai.providers.providers.registry import get_provider
from api.orchestrator.ai.providers.providers.base import (
    AlibabaCloudAdapter,
    GenerationResult,
    PageConfig,
)
from api.orchestrator.ai.providers.providers.rag_context import RAGContextBuilder
from api.orchestrator.ai.providers.catalog_loader import get_catalog, CatalogLoader

from api.orchestrator.preview.service.storage import get_storage
from api.telegram.service.telegram_service import notify_job_completed, send_message

from api.orchestrator.db.service.queue import get_message_queue
from api.orchestrator.models.dto import JobQueueMessage

from api.orchestrator.preview.rendering.html_renderer import render_page_with_layout

from api.orchestrator.worker.persistance.worker_persistance import (
    fetch_job,
    update_job_status,
    create_job_attempt,
    finish_job_attempt,
)
from api.telegram.persistence.telegram_persistence import get_user_session
from api.orchestrator.preview.service.expozy_publisher import ExpozyPublisher

setup_logging()
logger = get_logger(__name__)
settings = get_settings()

shutdown_event = asyncio.Event()

MAX_PAGE_RETRIES = 3
RETRY_DELAY_SECONDS = 0.5
DEFAULT_MAX_TOKENS = 8192
DEFAULT_TEMPERATURE = 0.5
CONTEXT_EXTRACTION_MAX_TOKENS = 2048
PAGE_SELECTION_MAX_TOKENS = 1024


# ─── Validator wrapper ───────────────────────────────────────────────────────

def _wrap_validator(data, page_type, schema_path=""):
    try:
        from api.orchestrator.ai.providers.validator import validate_template
        return validate_template(data=data, page_type=page_type)
    except ImportError:
        logger.warning("Validator not available, skipping validation")
        return None
    except Exception as e:
        logger.warning("Validator error", error=str(e))
        return None


# ─── Error hints ─────────────────────────────────────────────────────────────

def _build_error_hints(errors: List[str], hints_map: Dict[str, str]) -> str:
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


# =============================================================================
# STEP 1: BUSINESS CONTEXT EXTRACTION
# =============================================================================

async def extract_business_context(prompt, lang, provider, rag, catalog):
    schema_context = await rag.business_context_schema_context()  # ← await added

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
        response_schema=catalog.business_context_response_schema(),
    )

    result = await provider.generate(
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


# =============================================================================
# STEP 2: AI PAGE SELECTION
# =============================================================================

async def select_pages(prompt, business_context, lang, provider, rag, catalog):
    pages_context = await rag.page_selection_context(business_context)  # ← await added

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
        response_schema=catalog.page_selection_response_schema(),
    )

    result = await provider.generate(
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

    selected.update(catalog.global_type_ids())

    valid = set(catalog.all_page_type_ids())
    invalid = selected - valid
    if invalid:
        logger.warning("Unknown page types removed", invalid=invalid)
    selected = selected & valid

    for page_id in list(selected):
        selected.update(catalog.requires(page_id))

    return sorted(selected)


# =============================================================================
# STEP 3: SINGLE PAGE GENERATION
# =============================================================================

async def generate_page(prompt, page_type, business_context, lang, provider, rag, catalog, selected_pages=None):
    shared_rules = catalog.shared_rules_prompt()

    if catalog.is_global_type(page_type):
        rag_context = await rag.global_type_context(  # ← await added
            global_type=page_type,
            selected_pages=selected_pages or [],
        )

        nav_links = []
        for pid in (selected_pages or []):
            if not catalog.is_global_type(pid):
                try:
                    route = catalog.route(pid)
                    nav_links.append(f"  {route} → {pid}")
                except KeyError:
                    nav_links.append(f"  /{pid} → {pid}")

        output_file = catalog.output_file(page_type)

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
        rag_context = await rag.page_generation_context(  # ← await added
            page_type=page_type,
            business_context=business_context,
            prompt=prompt,
        )

        output_file = catalog.output_file(page_type)

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
        validate_fn=_wrap_validator,
        expect_html=True,
    )

    return await provider.generate(
        prompt=prompt,
        page_type=page_type,
        page_config=page_config,
        lang=lang,
    )


# =============================================================================
# STEP 3b: PAGE GENERATION WITH RETRIES + ERROR FEEDBACK
# =============================================================================

async def generate_page_with_retries(prompt, page_type, business_context, lang, provider, rag, catalog, selected_pages=None):
    last_result = None
    errors_from_previous = []
    previous_html = None
    hints_map = catalog.error_hints()

    for attempt in range(1, MAX_PAGE_RETRIES + 1):
        logger.info("Generating page", page_type=page_type, attempt=attempt)

        effective_prompt = prompt

        if errors_from_previous:
            error_lines = "\n".join(f"  {i+1}. {e}" for i, e in enumerate(errors_from_previous[:15]))
            hints = _build_error_hints(errors_from_previous, hints_map)

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

        result = await generate_page(
            prompt=effective_prompt,
            page_type=page_type,
            business_context=business_context,
            lang=lang,
            provider=provider,
            rag=rag,
            catalog=catalog,
            selected_pages=selected_pages,
        )
        last_result = result

        if result.success:
            logger.info("Page generated successfully", page_type=page_type, attempt=attempt, latency_ms=result.latency_ms)
            return result

        errors_from_previous = result.all_errors()
        previous_html = result.template if isinstance(result.template, str) else None

        logger.warning("Page generation failed", page_type=page_type, attempt=attempt, errors=errors_from_previous[:3])

        if not result.retryable:
            return result

        if attempt < MAX_PAGE_RETRIES:
            await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)

    logger.error("Page failed after all retries", page_type=page_type)
    return last_result


# =============================================================================
# STEP 4: FULL SITE GENERATION (parallel content pages)
# =============================================================================

def _collect_result(page_type, result, pages, all_errors):
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


async def generate_site(prompt, lang="bg"):
    provider = get_provider()
    rag = RAGContextBuilder()
    catalog = get_catalog()

    all_errors = []
    total_latency = 0
    results = {}

    logger.info("Step 1: extracting business context")
    business_context = await extract_business_context(prompt, lang, provider, rag, catalog)

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

    logger.info("Business context extracted", company=business_context.get("company_name"), type=business_context.get("business_type"))

    logger.info("Step 2: selecting pages")
    selected_pages = await select_pages(prompt, business_context, lang, provider, rag, catalog)
    logger.info("Pages selected", pages=selected_pages)

    generation_order = catalog.generation_order()

    content_pages = [p for p in generation_order if p in selected_pages and not catalog.is_global_type(p)]
    global_pages  = [p for p in generation_order if p in selected_pages and catalog.is_global_type(p)]

    for p in selected_pages:
        if p not in content_pages and p not in global_pages:
            (global_pages if catalog.is_global_type(p) else content_pages).append(p)

    pages = {}
    retryable = False

    logger.info("Step 3: generating content pages in parallel", count=len(content_pages))

    content_tasks = [
        generate_page_with_retries(
            prompt=prompt,
            page_type=pt,
            business_context=business_context,
            lang=lang,
            provider=provider,
            rag=rag,
            catalog=catalog,
        )
        for pt in content_pages
    ]
    content_results = await asyncio.gather(*content_tasks, return_exceptions=True)

    for pt, result in zip(content_pages, content_results):
        results[pt] = result
        if not isinstance(result, Exception):
            total_latency += result.latency_ms
        if _collect_result(pt, result, pages, all_errors):
            retryable = True

    logger.info("Step 4: generating global pages in parallel", count=len(global_pages))

    global_tasks = [
        generate_page_with_retries(
            prompt=prompt,
            page_type=pt,
            business_context=business_context,
            lang=lang,
            provider=provider,
            rag=rag,
            catalog=catalog,
            selected_pages=selected_pages,
        )
        for pt in global_pages
    ]
    global_results = await asyncio.gather(*global_tasks, return_exceptions=True)

    for pt, result in zip(global_pages, global_results):
        results[pt] = result
        if not isinstance(result, Exception):
            total_latency += result.latency_ms
        if _collect_result(pt, result, pages, all_errors):
            retryable = True

    required = set(catalog.required_page_ids()) | set(catalog.global_type_ids())
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


# =============================================================================
# JOB PROCESSING
# =============================================================================

async def process_job(job_id, attempt):
    logger.info("Processing job", job_id=str(job_id), attempt=attempt)

    job = await fetch_job(job_id)
    if not job:
        logger.error("Job not found", job_id=str(job_id))
        return True

    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
        logger.info("Job already finished", job_id=str(job_id), status=job.status)
        return True

    await update_job_status(job_id, JobStatus.RUNNING, increment_attempts=True)
    attempt_id = await create_job_attempt(job_id, attempt, settings.ai_provider)

    try:
        site = await generate_site(job.prompt_text)

        if not site["success"]:
            if site["retryable"] and attempt < settings.max_retries:
                await finish_job_attempt(attempt_id, AttemptOutcome.FAIL, error_detail="; ".join(site["errors"][:5]))
                await update_job_status(
                    job_id,
                    JobStatus.QUEUED,
                    error_message="; ".join(site["errors"][:3]),
                    raw_ai_response={
                        "errors": site["errors"],
                        "latency_ms": site["total_latency_ms"],
                        "business_context": site.get("business_context"),
                        "selected_pages": site.get("selected_pages"),
                    },
                )
                return False

            await finish_job_attempt(attempt_id, AttemptOutcome.FAIL, error_detail="; ".join(site["errors"][:5]))
            await update_job_status(
                job_id,
                JobStatus.FAILED,
                error_message="; ".join(site["errors"][:3]),
                raw_ai_response={"errors": site["errors"]},
                validation_errors={"errors": site["errors"]},
            )
            return True

        templates = site["pages"]
        business_context = site.get("business_context", {})
        lang = business_context.get("primary_language", "bg")
        company_name = business_context.get("company_name", "EXPOZY Preview")

        catalog = get_catalog()
        global_ids = set(catalog.global_type_ids())

        header_html = templates.get("header")
        footer_html = templates.get("footer")

        html_pages = {}
        for page_type, content in templates.items():
            if page_type not in global_ids and content is not None:
                html_pages[page_type] = render_page_with_layout(
                    content=content,
                    header=header_html,
                    footer=footer_html,
                    title=company_name,
                    lang=lang,
                )

        logger.info("Rendered pages", pages=list(html_pages.keys()))

        storage = get_storage()
        bundle_id = await storage.create_bundle(
            template=templates,
            html_content=html_pages,
            job_id=job_id,
        )

        preview_url = f"/p/{bundle_id}/index.html"

        await finish_job_attempt(attempt_id, AttemptOutcome.SUCCESS)
        await update_job_status(
            job_id,
            JobStatus.COMPLETED,
            bundle_id=bundle_id,
            preview_url=preview_url,
            raw_ai_response={
                "pages": list(templates.keys()),
                "_business_context": business_context,
                "_selected_pages": site.get("selected_pages"),
            },
        )

        await notify_job_completed(
            job.chat_id,
            preview_url,
            settings.preview_base_url,
            html_pages=html_pages,
            bundle_id=str(bundle_id),
        )

        try:
            telegram_id = job.user_id or job.chat_id

            async with get_db_session() as db:
                session = await get_user_session(db, telegram_id)

            if session:
                publisher = ExpozyPublisher(
                    project_url=session.project_url,
                    saas_key=session.saas_key,
                    token=session.token,
                )
                pushed = await publisher.push_all(list(html_pages.items()))
                url_list = "\n".join(f"📄 {p['title']}: {p['url']}" for p in pushed)
                await send_message(job.chat_id, f"✅ Pages are live on your Expozy store:\n\n{url_list}")
                logger.info("Pages pushed to Expozy", count=len(pushed))
            else:
                logger.warning("No Expozy session found for user", telegram_id=telegram_id)

        except Exception as e:
            logger.error("Failed to push to Expozy", error=str(e), exc_info=e)

        logger.info(
            "Job completed",
            job_id=str(job_id),
            bundle_id=str(bundle_id),
            pages=list(html_pages.keys()),
            selected_pages=site.get("selected_pages"),
            total_latency_ms=site["total_latency_ms"],
        )
        return True

    except Exception as e:
        logger.error("Job processing error", job_id=str(job_id), error=str(e), exc_info=e)
        await finish_job_attempt(attempt_id, AttemptOutcome.FAIL, error_detail=str(e))

        if attempt < settings.max_retries:
            await update_job_status(job_id, JobStatus.QUEUED, error_message=str(e))
            return False

        await update_job_status(job_id, JobStatus.FAILED, error_message=str(e))
        return True


# =============================================================================
# MESSAGE HANDLING & MAIN LOOP
# =============================================================================

async def handle_message(message):
    completed = await process_job(message.job_id, message.attempt)

    if not completed and message.attempt < settings.max_retries:
        delay = min(settings.retry_base_delay * (2 ** (message.attempt - 1)), settings.retry_max_delay)
        logger.info("Scheduling retry", job_id=str(message.job_id), next_attempt=message.attempt + 1, delay=delay)

        async with get_message_queue() as mq:
            await mq.publish_job_delayed(message.job_id, message.attempt + 1, delay)


async def run_worker():
    logger.info("Worker starting (RAG-powered, parallel HTML generation)")

    async with get_message_queue() as mq:
        consumer_task = asyncio.create_task(mq.consume(handle_message))
        await shutdown_event.wait()

        logger.info("Shutting down worker")
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass

    logger.info("Worker stopped")


def handle_signals():
    def signal_handler(signum, frame):
        logger.info("Received shutdown signal", signal=signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


def main():
    handle_signals()
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker interrupted")
    except Exception as e:
        logger.error("Worker crashed", error=str(e), exc_info=e)
        sys.exit(1)


if __name__ == "__main__":
    main()