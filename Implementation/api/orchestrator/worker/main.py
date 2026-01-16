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
    Render a template package to HTML.
    
    This is a simplified renderer. In production, you would use
    a proper templating engine or call the EXPOZY rendering service.
    """
    metadata = template.get("metadata", {})
    theme = template.get("theme", {})
    sections = template.get("sections", [])
    
    primary_color = theme.get("primaryColor", "#3B82F6")
    
    # Build sections HTML
    sections_html = []
    for section in sections:
        sec_type = section.get("type", "content")
        title = section.get("title", "")
        subtitle = section.get("subtitle", "")
        content = section.get("content", "")
        class_name = section.get("className", "")
        
        buttons_html = ""
        for btn in section.get("buttons", []):
            variant = btn.get("variant", "primary")
            label = btn.get("label", "Button")
            href = btn.get("href", "#")
            btn_class = "btn-primary" if variant == "primary" else "btn-secondary"
            buttons_html += f'<a href="{href}" class="btn {btn_class}">{label}</a>'
        
        section_html = f"""
        <section class="section section-{sec_type} {class_name}">
            {f'<h2 class="section-title">{title}</h2>' if title else ''}
            {f'<p class="section-subtitle">{subtitle}</p>' if subtitle else ''}
            {f'<div class="section-content">{content}</div>' if content else ''}
            {f'<div class="section-buttons">{buttons_html}</div>' if buttons_html else ''}
        </section>
        """
        sections_html.append(section_html)
    
    # Full HTML document
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' https://fonts.gstatic.com;">
    <title>{metadata.get('name', 'Generated Page')}</title>
    <meta name="description" content="{metadata.get('description', '')}">
    <style>
        :root {{
            --primary-color: {primary_color};
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            color: #1f2937;
        }}
        .section {{
            padding: 4rem 2rem;
            max-width: 1200px;
            margin: 0 auto;
        }}
        .section-hero {{
            text-align: center;
            background: linear-gradient(135deg, var(--primary-color), #8b5cf6);
            color: white;
            padding: 6rem 2rem;
            max-width: none;
        }}
        .section-title {{
            font-size: 2.5rem;
            font-weight: 700;
            margin-bottom: 1rem;
        }}
        .section-subtitle {{
            font-size: 1.25rem;
            opacity: 0.9;
            margin-bottom: 2rem;
        }}
        .section-content {{
            font-size: 1.1rem;
            max-width: 800px;
            margin: 0 auto;
        }}
        .section-buttons {{
            display: flex;
            gap: 1rem;
            justify-content: center;
            flex-wrap: wrap;
            margin-top: 2rem;
        }}
        .btn {{
            display: inline-block;
            padding: 0.75rem 2rem;
            border-radius: 0.5rem;
            text-decoration: none;
            font-weight: 600;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        .btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }}
        .btn-primary {{
            background: white;
            color: var(--primary-color);
        }}
        .btn-secondary {{
            background: transparent;
            color: white;
            border: 2px solid white;
        }}
        .section-products, .section-features {{
            background: #f9fafb;
        }}
        .section-cta {{
            text-align: center;
            background: #1f2937;
            color: white;
            max-width: none;
        }}
        .section-cta .btn-primary {{
            background: var(--primary-color);
            color: white;
        }}
        @media (max-width: 768px) {{
            .section {{ padding: 3rem 1rem; }}
            .section-title {{ font-size: 2rem; }}
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
