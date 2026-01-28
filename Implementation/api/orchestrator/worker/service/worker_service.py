from __future__ import annotations

import asyncio
import signal
import sys
from typing import Optional
from uuid import UUID

from shared.config import get_settings
from shared.utils import setup_logging, get_logger

from api.orchestrator.db.models import JobStatus, AttemptOutcome
from api.orchestrator.ai.providers.ai_provider import get_provider, GenerationResult
from api.orchestrator.preview.service.sanitizer import get_sanitizer
from api.orchestrator.preview.service.storage import get_storage
from api.telegram.telegram_client import notify_job_completed

from api.orchestrator.db.service.queue import get_message_queue
from api.orchestrator.models.dto import JobQueueMessage  

from api.orchestrator.preview.rendering.html_renderer import render_template_to_html

from api.orchestrator.worker.persistance.worker_persistance import (
    fetch_job,
    update_job_status,
    create_job_attempt,
    finish_job_attempt,
)



setup_logging()
logger = get_logger(__name__)
settings = get_settings()

shutdown_event = asyncio.Event()


async def call_ai_provider(prompt: str) -> GenerationResult:
    provider = get_provider()
    return await provider.generate(prompt)


async def process_job(job_id: UUID, attempt: int) -> bool:
    """
    Business logic orchestration.
    Returns True if finished (success or permanent failure),
    False if should retry.
    """
    logger.info("Processing job", job_id=str(job_id), attempt=attempt)

    job = await fetch_job(job_id)
    if not job:
        logger.error("Job not found", job_id=str(job_id))
        return True

    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
        logger.info("Job already finished", job_id=str(job_id), status=job.status)
        return True

    await update_job_status(job_id, JobStatus.RUNNING, increment_attempts=True)

    provider_name = settings.ai_provider
    attempt_id = await create_job_attempt(job_id, attempt, provider_name)

    try:
        result = await call_ai_provider(job.prompt_text)

        if not result.success:
            # retryable failure?
            if result.retryable and attempt < settings.max_retries:
                await finish_job_attempt(
                    attempt_id,
                    AttemptOutcome.FAIL,
                    error_detail=result.error,
                    provider_status_code=429 if "rate" in (result.error or "").lower() else None,
                )
                await update_job_status(
                    job_id,
                    JobStatus.QUEUED,
                    error_message=result.error,
                    raw_ai_response={"error": result.error, "raw": result.raw_response},
                )
                return False

            # permanent failure
            await finish_job_attempt(attempt_id, AttemptOutcome.FAIL, error_detail=result.error)
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

        sanitizer = get_sanitizer()
        sanitized_template = sanitizer.sanitize_template(template)

        html_content = render_template_to_html(sanitized_template)

        storage = get_storage()
        bundle_id = await storage.create_bundle(
            template=sanitized_template,
            html_content=html_content,
            job_id=job_id,
        )

        preview_url = f"/p/{bundle_id}/index.html"

        await finish_job_attempt(attempt_id, AttemptOutcome.SUCCESS)

        await update_job_status(
            job_id,
            JobStatus.COMPLETED,
            bundle_id=bundle_id,
            preview_url=preview_url,
            raw_ai_response=template,
        )

        await notify_job_completed(job.chat_id, preview_url, settings.preview_base_url)

        logger.info("Job completed", job_id=str(job_id), bundle_id=str(bundle_id))
        return True

    except Exception as e:
        logger.error("Job processing error", job_id=str(job_id), error=str(e), exc_info=e)

        await finish_job_attempt(attempt_id, AttemptOutcome.FAIL, error_detail=str(e))

        if attempt < settings.max_retries:
            await update_job_status(job_id, JobStatus.QUEUED, error_message=str(e))
            return False

        await update_job_status(job_id, JobStatus.FAILED, error_message=str(e))
        return True


async def handle_message(message: JobQueueMessage) -> None:
    job_id = message.job_id
    attempt = message.attempt

    completed = await process_job(job_id, attempt)

    if not completed and attempt < settings.max_retries:
        delay = min(
            settings.retry_base_delay * (2 ** (attempt - 1)),
            settings.retry_max_delay,
        )

        logger.info("Scheduling retry", job_id=str(job_id), next_attempt=attempt + 1, delay_seconds=delay)

        async with get_message_queue() as mq:
            await mq.publish_job_delayed(job_id, attempt + 1, delay)


async def run_worker() -> None:
    logger.info("Worker starting", max_retries=settings.max_retries)

    async with get_message_queue() as mq:
        consumer_task = asyncio.create_task(mq.consume(handle_message))
        await shutdown_event.wait()

        logger.info("Shutdown signal received, stopping consumer")
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass

    logger.info("Worker stopped")


def handle_signals() -> None:
    def signal_handler(signum, frame):
        logger.info("Received signal", signal=signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


def main() -> None:
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
