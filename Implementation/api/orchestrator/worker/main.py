"""
Worker service for processing AI generation jobs.

Consumes jobs from RabbitMQ queue and:
1. Fetches job from database
2. Creates job_attempt record
3. Calls AI provider
4. Validates and sanitizes response
5. Creates bundle on filesystem
6. Updates job with result
7. Implements retry with exponential backoff on failures
"""

import asyncio
import signal
import sys
from api.telegram.service.telegram import notify_job_completed
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from shared.config import get_settings
from api.orchestrator.db.models import Job, JobStatus, JobAttempt, AttemptOutcome
from api.orchestrator.models.schemas import JobQueueMessage
from api.orchestrator.ai.providers.ai_provider import get_provider, GenerationResult
from api.orchestrator.db.session import get_db_session
from api.orchestrator.db.service.queue import get_message_queue
from api.orchestrator.preview.service.sanitizer import get_sanitizer
from api.orchestrator.preview.service.storage import get_storage

from shared.utils import setup_logging, get_logger

# Initialize
setup_logging()
logger = get_logger(__name__)
settings = get_settings()

# Shutdown flag
shutdown_event = asyncio.Event()


# =============================================================================
# HTML RENDERER
# =============================================================================

def render_template_to_html(template: dict) -> str:
    """
    Render a template package (metadata/theme/sections) to a single HTML page.
    Supports section types used by your AI JSON example:
    hero, features, products, testimonials, cta, form, footer (+ fallback).
    """
    import html as _html
    import re as _re
    from urllib.parse import urlparse

    metadata = template.get("metadata", {}) or {}
    theme = template.get("theme", {}) or {}
    sections = template.get("sections", []) or []

    primary_color = theme.get("primaryColor", "#3B82F6")
    dark_mode = bool(theme.get("darkMode", False))

    # Some AI outputs use pageType/page_type
    page_title = metadata.get("title") or metadata.get("name") or "Generated Page"
    page_desc = metadata.get("description") or ""

    def esc(s: object) -> str:
        return _html.escape("" if s is None else str(s), quote=True)

    def safe_class(s: object) -> str:
        s = "" if s is None else str(s)
        # keep only safe class characters
        s = _re.sub(r"[^a-zA-Z0-9_\- ]+", "", s).strip()
        return s

    def safe_url(u: object) -> str:
        """
        Allow http(s) only. Return empty string if unsafe.
        (Your preview CSP may still block remote images.)
        """
        u = "" if u is None else str(u).strip()
        if not u:
            return ""
        try:
            p = urlparse(u)
            if p.scheme in ("http", "https"):
                return u.replace('"', "%22").replace("'", "%27")
        except Exception:
            pass
        return ""

    def render_buttons(buttons: list) -> str:
        out = []
        for btn in buttons or []:
            variant = (btn.get("variant") or "primary").lower()
            # your AI example uses "text", not "label"
            label = btn.get("text") or btn.get("label") or "Button"
            href = btn.get("href") or "#"

            if variant in ("outline", "secondary"):
                btn_class = "btn-secondary"
            else:
                btn_class = "btn-primary"

            out.append(
                f'<a href="{esc(href)}" class="btn {btn_class}">{esc(label)}</a>'
            )
        return "".join(out)

    def render_hero(section: dict) -> str:
        title = section.get("title", "")
        subtitle = section.get("subtitle", "")
        bg = safe_url(section.get("backgroundImage"))
        style = ""
        if bg:
            style = (
                " style=\""
                f"background-image:url('{bg}');"
                "background-size:cover;"
                "background-position:center;"
                "\""
            )

        return f"""
        <section class="section section-hero {safe_class(section.get('className'))}"{style}>
            <div class="hero-overlay"></div>
            <div class="hero-inner">
                {f'<h1 class="hero-title">{esc(title)}</h1>' if title else ''}
                {f'<p class="hero-subtitle">{esc(subtitle)}</p>' if subtitle else ''}
                <div class="section-buttons">{render_buttons(section.get("buttons", []))}</div>
            </div>
        </section>
        """

    def render_features(section: dict) -> str:
        title = section.get("title", "")
        subtitle = section.get("subtitle", "")
        cols = int(section.get("columns") or 3)
        cols = max(1, min(cols, 4))
        items = section.get("items", []) or []

        cards = []
        for item in items:
            it_title = item.get("title", "")
            it_content = item.get("content", "")
            it_icon = item.get("icon", "")
            cards.append(f"""
                <div class="card">
                    {f'<div class="card-icon">{esc(it_icon)}</div>' if it_icon else ''}
                    {f'<div class="card-title">{esc(it_title)}</div>' if it_title else ''}
                    {f'<div class="card-body">{esc(it_content)}</div>' if it_content else ''}
                </div>
            """)

        return f"""
        <section class="section section-features {safe_class(section.get('className'))}">
            {f'<h2 class="section-title">{esc(title)}</h2>' if title else ''}
            {f'<p class="section-subtitle">{esc(subtitle)}</p>' if subtitle else ''}
            <div class="grid" style="--cols:{cols};">
                {''.join(cards) if cards else '<div class="muted">No feature items provided.</div>'}
            </div>
        </section>
        """

    def render_products_like(section: dict, kind: str) -> str:
        """
        products/testimonials: if section.items exists -> render it.
        if only dataSource exists -> render placeholders (because no JS runs in preview).
        """
        title = section.get("title", "")
        subtitle = section.get("subtitle", "")
        cols = int(section.get("columns") or 3)
        cols = max(1, min(cols, 4))

        items = section.get("items")
        ds = section.get("dataSource")

        cards = []
        if isinstance(items, list) and items:
            for item in items:
                # flexible keys (product/testimonial)
                it_title = item.get("title") or item.get("name") or ""
                it_sub = item.get("subtitle") or item.get("role") or item.get("price") or ""
                it_content = item.get("content") or item.get("text") or item.get("description") or ""
                cards.append(f"""
                    <div class="card">
                        {f'<div class="card-title">{esc(it_title)}</div>' if it_title else ''}
                        {f'<div class="card-meta">{esc(it_sub)}</div>' if it_sub else ''}
                        {f'<div class="card-body">{esc(it_content)}</div>' if it_content else ''}
                    </div>
                """)
        else:
            # Placeholder if you didn't pre-resolve dataSources in the worker
            label = f"Loaded from dataSource: {ds}" if ds else "No items/dataSource provided"
            for i in range(cols * 2):
                cards.append(f"""
                    <div class="card">
                        <div class="card-title">{esc(kind.title())} Item {i+1}</div>
                        <div class="card-body muted">{esc(label)}</div>
                    </div>
                """)

        return f"""
        <section class="section section-{kind} {safe_class(section.get('className'))}">
            {f'<h2 class="section-title">{esc(title)}</h2>' if title else ''}
            {f'<p class="section-subtitle">{esc(subtitle)}</p>' if subtitle else ''}
            <div class="grid" style="--cols:{cols};">
                {''.join(cards)}
            </div>
        </section>
        """

    def render_cta(section: dict) -> str:
        title = section.get("title", "")
        content = section.get("content", "")
        return f"""
        <section class="section section-cta {safe_class(section.get('className'))}">
            <div class="cta-inner">
                {f'<h2 class="section-title">{esc(title)}</h2>' if title else ''}
                {f'<div class="section-content">{esc(content)}</div>' if content else ''}
                <div class="section-buttons">{render_buttons(section.get("buttons", []))}</div>
            </div>
        </section>
        """

    def render_form(section: dict) -> str:
        title = section.get("title", "")
        subtitle = section.get("subtitle", "")
        fields = section.get("fields", []) or []
        # No JS + no backend handler here; keep it inert
        form_fields = []
        for f in fields:
            name = f.get("name") or "field"
            label = f.get("label") or name
            ftype = f.get("type") or "text"
            placeholder = f.get("placeholder") or ""
            required = "required" if f.get("required") else ""
            form_fields.append(f"""
                <label class="form-field">
                    <span class="form-label">{esc(label)}</span>
                    <input class="input" name="{esc(name)}" type="{esc(ftype)}" placeholder="{esc(placeholder)}" {required}/>
                </label>
            """)

        return f"""
        <section class="section section-form {safe_class(section.get('className'))}">
            {f'<h2 class="section-title">{esc(title)}</h2>' if title else ''}
            {f'<p class="section-subtitle">{esc(subtitle)}</p>' if subtitle else ''}
            <form class="form" action="#" method="post">
                {''.join(form_fields)}
                <button type="submit" class="btn btn-primary">Submit</button>
            </form>
            <div class="muted form-note">Note: form submit is disabled in preview (no backend).</div>
        </section>
        """

    def render_footer(section: dict) -> str:
        content = section.get("content", "")
        items = section.get("items", []) or []
        links = []
        for it in items:
            t = it.get("title") or ""
            href = it.get("href") or "#"
            if t:
                links.append(f'<a class="footer-link" href="{esc(href)}">{esc(t)}</a>')
        return f"""
        <footer class="section section-footer {safe_class(section.get('className'))}">
            <div class="footer-inner">
                {f'<div class="footer-content">{esc(content)}</div>' if content else ''}
                <div class="footer-links">{''.join(links)}</div>
            </div>
        </footer>
        """

    def render_default(section: dict) -> str:
        sec_type = section.get("type", "content")
        title = section.get("title", "")
        subtitle = section.get("subtitle", "")
        content = section.get("content", "")
        return f"""
        <section class="section section-{esc(sec_type)} {safe_class(section.get('className'))}">
            {f'<h2 class="section-title">{esc(title)}</h2>' if title else ''}
            {f'<p class="section-subtitle">{esc(subtitle)}</p>' if subtitle else ''}
            {f'<div class="section-content">{esc(content)}</div>' if content else ''}
            <div class="section-buttons">{render_buttons(section.get("buttons", []))}</div>
        </section>
        """

    sections_html = []
    for section in sections:
        sec_type = (section.get("type") or "content").lower()

        if sec_type == "hero":
            sections_html.append(render_hero(section))
        elif sec_type == "features":
            sections_html.append(render_features(section))
        elif sec_type == "products":
            sections_html.append(render_products_like(section, "products"))
        elif sec_type == "testimonials":
            sections_html.append(render_products_like(section, "testimonials"))
        elif sec_type == "cta":
            sections_html.append(render_cta(section))
        elif sec_type == "form":
            sections_html.append(render_form(section))
        elif sec_type == "footer":
            sections_html.append(render_footer(section))
        else:
            sections_html.append(render_default(section))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{esc(page_title)}</title>
    <meta name="description" content="{esc(page_desc)}">
    <style>
        :root {{
            --primary-color: {esc(primary_color)};
            --bg: {"#0b1220" if dark_mode else "#ffffff"};
            --fg: {"#e5e7eb" if dark_mode else "#111827"};
            --muted: {"#9ca3af" if dark_mode else "#6b7280"};
            --card: {"#0f172a" if dark_mode else "#f9fafb"};
            --border: {"rgba(255,255,255,0.08)" if dark_mode else "rgba(0,0,0,0.08)"};
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            line-height: 1.6;
            background: var(--bg);
            color: var(--fg);
        }}

        .section {{
            padding: 4rem 2rem;
            max-width: 1200px;
            margin: 0 auto;
        }}

        .section-title {{
            font-size: 2.2rem;
            font-weight: 800;
            margin-bottom: 0.75rem;
        }}

        .section-subtitle {{
            font-size: 1.1rem;
            color: var(--muted);
            margin-bottom: 1.75rem;
        }}

        .section-content {{
            font-size: 1.05rem;
            color: var(--fg);
            max-width: 900px;
        }}

        .muted {{ color: var(--muted); }}

        /* HERO */
        .section-hero {{
            position: relative;
            text-align: center;
            color: white;
            padding: 6rem 2rem;
            max-width: none;
            border-bottom: 1px solid var(--border);
            background: linear-gradient(135deg, var(--primary-color), #8b5cf6);
        }}
        .hero-overlay {{
            position:absolute; inset:0;
            background: rgba(0,0,0,0.45);
        }}
        .hero-inner {{
            position: relative;
            max-width: 900px;
            margin: 0 auto;
        }}
        .hero-title {{
            font-size: 3rem;
            font-weight: 900;
            line-height: 1.1;
            margin-bottom: 1rem;
        }}
        .hero-subtitle {{
            font-size: 1.2rem;
            opacity: 0.95;
        }}

        /* BUTTONS */
        .section-buttons {{
            display: flex;
            gap: 1rem;
            justify-content: center;
            flex-wrap: wrap;
            margin-top: 2rem;
        }}
        .btn {{
            display: inline-block;
            padding: 0.75rem 1.5rem;
            border-radius: 0.75rem;
            text-decoration: none;
            font-weight: 700;
            border: 1px solid transparent;
        }}
        .btn-primary {{
            background: white;
            color: #111827;
        }}
        .btn-secondary {{
            background: transparent;
            color: white;
            border-color: rgba(255,255,255,0.7);
        }}

        /* GRID/CARDS */
        .grid {{
            display: grid;
            grid-template-columns: repeat(var(--cols, 3), minmax(0, 1fr));
            gap: 1rem;
        }}
        .card {{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 1rem;
            padding: 1.25rem;
        }}
        .card-icon {{
            color: var(--muted);
            font-size: 0.9rem;
            margin-bottom: 0.5rem;
        }}
        .card-title {{
            font-weight: 800;
            margin-bottom: 0.35rem;
        }}
        .card-meta {{
            color: var(--muted);
            font-size: 0.9rem;
            margin-bottom: 0.75rem;
        }}
        .card-body {{
            color: var(--fg);
            font-size: 0.98rem;
        }}

        /* CTA */
        .section-cta {{
            max-width: none;
            background: #111827;
            color: white;
        }}
        .cta-inner {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        .section-cta .section-subtitle,
        .section-cta .section-content {{
            color: rgba(255,255,255,0.85);
        }}
        .section-cta .btn-primary {{
            background: var(--primary-color);
            color: white;
        }}

        /* FORM */
        .form {{
            display: grid;
            gap: 1rem;
            max-width: 520px;
        }}
        .form-field {{
            display: grid;
            gap: 0.4rem;
        }}
        .form-label {{
            color: var(--muted);
            font-size: 0.9rem;
            font-weight: 600;
        }}
        .input {{
            padding: 0.75rem 0.9rem;
            border-radius: 0.75rem;
            border: 1px solid var(--border);
            background: var(--card);
            color: var(--fg);
            outline: none;
        }}
        .form-note {{
            margin-top: 1rem;
            font-size: 0.9rem;
        }}

        /* FOOTER */
        .section-footer {{
            max-width: none;
            border-top: 1px solid var(--border);
            padding-top: 2rem;
            padding-bottom: 2rem;
        }}
        .footer-inner {{
            max-width: 1200px;
            margin: 0 auto;
            display: grid;
            gap: 1rem;
        }}
        .footer-links {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem 1rem;
        }}
        .footer-link {{
            color: var(--muted);
            text-decoration: none;
        }}
        .footer-link:hover {{
            color: var(--fg);
        }}

        @media (max-width: 900px) {{
            .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
        }}
        @media (max-width: 600px) {{
            .section {{ padding: 3rem 1rem; }}
            .grid {{ grid-template-columns: 1fr; }}
            .hero-title {{ font-size: 2.2rem; }}
        }}
    </style>
</head>
<body>
    {''.join(sections_html)}
</body>
</html>"""

    return html



# =============================================================================
# JOB PROCESSOR
# =============================================================================

async def fetch_job(job_id: UUID) -> Optional[Job]:
    """Fetch job from database."""
    async with get_db_session() as db:
        result = await db.execute(
            select(Job)
            .options(selectinload(Job.attempts))
            .where(Job.id == job_id)
        )
        return result.scalar_one_or_none()


async def update_job_status(
    job_id: UUID,
    status: JobStatus,
    *,
    bundle_id: Optional[UUID] = None,
    preview_url: Optional[str] = None,
    error_message: Optional[str] = None,
    raw_ai_response: Optional[dict] = None,
    validation_errors: Optional[dict] = None,
    increment_attempts: bool = False,
) -> None:
    """Update job status in database."""
    async with get_db_session() as db:
        values = {
            "status": status,
            "updated_at": datetime.now(timezone.utc),
        }
        
        if bundle_id is not None:
            values["bundle_id"] = bundle_id
        if preview_url is not None:
            values["preview_url"] = preview_url
        if error_message is not None:
            values["error_message"] = error_message
        if raw_ai_response is not None:
            values["raw_ai_response"] = raw_ai_response
        if validation_errors is not None:
            values["validation_errors"] = validation_errors
        
        if increment_attempts:
            # Use raw SQL for atomic increment
            stmt = update(Job).where(Job.id == job_id).values(
                **values,
                attempt_count=Job.attempt_count + 1,
            )
        else:
            stmt = update(Job).where(Job.id == job_id).values(**values)
        
        await db.execute(stmt)
        await db.commit()


async def create_job_attempt(
    job_id: UUID,
    attempt_no: int,
    provider_name: str,
) -> int:
    """Create a new job attempt record."""
    async with get_db_session() as db:
        attempt = JobAttempt(
            job_id=job_id,
            attempt_no=attempt_no,
            provider_name=provider_name,
        )
        db.add(attempt)
        await db.flush()
        attempt_id = attempt.id
        await db.commit()
        return attempt_id


async def finish_job_attempt(
    attempt_id: int,
    outcome: AttemptOutcome,
    error_detail: Optional[str] = None,
    provider_status_code: Optional[int] = None,
) -> None:
    """Mark a job attempt as finished."""
    async with get_db_session() as db:
        result = await db.execute(
            select(JobAttempt).where(JobAttempt.id == attempt_id)
        )
        attempt = result.scalar_one_or_none()
        
        if attempt:
            attempt.finished_at = datetime.now(timezone.utc)
            attempt.outcome = outcome
            attempt.error_detail = error_detail
            attempt.provider_status_code = provider_status_code
            
            # Calculate duration
            if attempt.started_at:
                delta = attempt.finished_at - attempt.started_at
                attempt.duration_ms = int(delta.total_seconds() * 1000)
            
            await db.commit()


async def call_ai_provider(prompt: str) -> GenerationResult:
    """Call AI provider."""
    provider = get_provider()
    return await provider.generate(prompt)


async def process_job(job_id: UUID, attempt: int) -> bool:
    """
    Process a single job.
    
    Returns True if job completed (success or permanent failure).
    Returns False if job should be retried.
    """
    logger.info("Processing job", job_id=str(job_id), attempt=attempt)
    
    # Fetch job
    job = await fetch_job(job_id)
    if not job:
        logger.error("Job not found", job_id=str(job_id))
        return True  # Don't retry non-existent jobs
    
    # Check if already completed
    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
        logger.info("Job already finished", job_id=str(job_id), status=job.status)
        return True
    
    # Update status to running and increment attempt count
    await update_job_status(
        job_id,
        JobStatus.RUNNING,
        increment_attempts=True,
    )
    
    # Create job attempt record
    provider_name = settings.ai_provider
    attempt_id = await create_job_attempt(job_id, attempt, provider_name)
    
    provider_status_code: Optional[int] = None
    
    try:
        # Call AI provider
        result = await call_ai_provider(job.prompt_text)
        
        # Extract status code if available
        if result.raw_response and "status_code" in str(result.raw_response):
            # Try to parse status code from response
            pass
        
        if not result.success:
            # Check if we should retry
            if result.retryable and attempt < settings.max_retries:
                logger.warning(
                    "AI generation failed, will retry",
                    job_id=str(job_id),
                    attempt=attempt,
                    error=result.error,
                )
                
                # Finish attempt as failed
                await finish_job_attempt(
                    attempt_id,
                    AttemptOutcome.FAIL,
                    error_detail=result.error,
                    provider_status_code=429 if "rate" in (result.error or "").lower() else None,
                )
                
                # Update job to queued for retry
                await update_job_status(
                    job_id,
                    JobStatus.QUEUED,
                    error_message=result.error,
                    raw_ai_response={"error": result.error, "raw": result.raw_response},
                )
                return False  # Signal retry needed
            
            # Permanent failure
            logger.error(
                "AI generation failed permanently",
                job_id=str(job_id),
                error=result.error,
            )
            
            await finish_job_attempt(
                attempt_id,
                AttemptOutcome.FAIL,
                error_detail=result.error,
            )
            
            await update_job_status(
                job_id,
                JobStatus.FAILED,
                error_message=result.error,
                raw_ai_response={"error": result.error, "raw": result.raw_response},
                validation_errors={"errors": result.validation.all_errors()} if result.validation else None,
            )
            return True
        
        template = result.template
        if not template:
            raise ValueError("AI returned success but no template")
        
        # Sanitize template
        sanitizer = get_sanitizer()
        sanitized_template = sanitizer.sanitize_template(template)
        
        # Render to HTML
        html_content = render_template_to_html(sanitized_template)
        
        # Store bundle
        storage = get_storage()
        bundle_id = await storage.create_bundle(
            template=sanitized_template,
            html_content=html_content,
            job_id=job_id,
        )
        
        # Build preview URL
        preview_url = f"/p/{bundle_id}/index.html"
        
        # Finish attempt as success
        await finish_job_attempt(
            attempt_id,
            AttemptOutcome.SUCCESS,
        )
        
        # Update job as completed
        await update_job_status(
            job_id,
            JobStatus.COMPLETED,
            bundle_id=bundle_id,
            preview_url=preview_url,
            raw_ai_response=template,
        )
        
        logger.info(
            "Job completed successfully",
            job_id=str(job_id),
            bundle_id=str(bundle_id),
        )
        
# Send Telegram notification with preview link
        await notify_job_completed(
            job.chat_id,
            preview_url,
            settings.preview_base_url,
        )        
        return True
        
    except Exception as e:
        logger.error(
            "Job processing error",
            job_id=str(job_id),
            error=str(e),
            exc_info=e,
        )
        
        # Finish attempt as failed
        await finish_job_attempt(
            attempt_id,
            AttemptOutcome.FAIL,
            error_detail=str(e),
        )
        
        if attempt < settings.max_retries:
            await update_job_status(
                job_id,
                JobStatus.QUEUED,
                error_message=str(e),
            )
            return False  # Retry
        
        await update_job_status(
            job_id,
            JobStatus.FAILED,
            error_message=str(e),
        )
        return True


# =============================================================================
# MESSAGE HANDLER
# =============================================================================

async def handle_message(message: JobQueueMessage) -> None:
    """Handle a job queue message."""
    job_id = message.job_id
    attempt = message.attempt
    
    completed = await process_job(job_id, attempt)
    
    if not completed and attempt < settings.max_retries:
        # Schedule retry with exponential backoff
        delay = min(
            settings.retry_base_delay * (2 ** (attempt - 1)),
            settings.retry_max_delay,
        )
        
        logger.info(
            "Scheduling retry",
            job_id=str(job_id),
            next_attempt=attempt + 1,
            delay_seconds=delay,
        )
        
        async with get_message_queue() as mq:
            await mq.publish_job_delayed(job_id, attempt + 1, delay)


# =============================================================================
# MAIN WORKER LOOP
# =============================================================================

async def run_worker() -> None:
    """Main worker loop."""
    logger.info("Worker starting", max_retries=settings.max_retries)
    
    async with get_message_queue() as mq:
        # Create a task that will be cancelled on shutdown
        consumer_task = asyncio.create_task(mq.consume(handle_message))
        
        # Wait for shutdown signal
        await shutdown_event.wait()
        
        logger.info("Shutdown signal received, stopping consumer")
        consumer_task.cancel()
        
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass
    
    logger.info("Worker stopped")


def handle_signals() -> None:
    """Set up signal handlers for graceful shutdown."""
    def signal_handler(signum, frame):
        logger.info("Received signal", signal=signum)
        shutdown_event.set()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


def main() -> None:
    """Entry point."""
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
