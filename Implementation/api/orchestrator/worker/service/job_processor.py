"""
Job Processor
Owns the full job lifecycle: fetch → generate → render → store → notify → publish.
"""

from typing import List, Tuple

from api.orchestrator.db.models import JobStatus, AttemptOutcome
from api.orchestrator.db.session import get_db_session
from api.orchestrator.ai.providers.catalog_loader import get_catalog
from api.orchestrator.preview.service.storage import get_storage
from api.orchestrator.preview.rendering.html_renderer import render_page_with_layout
from api.orchestrator.preview.service.expozy_publisher import ExpozyPublisher
from api.orchestrator.worker.persistance.worker_persistance import (
    fetch_job,
    update_job_status,
    create_job_attempt,
    finish_job_attempt,
)
from api.telegram.service.messaging import notify_job_completed, send_message
from api.telegram.persistence.telegram_persistence import get_user_session

from .site_generator import SiteGenerator

from shared.config import get_settings
from shared.utils import get_logger

logger = get_logger(__name__)
settings = get_settings()


class JobProcessor:
    def __init__(self):
        self.site_generator = SiteGenerator()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render_pages(
        self,
        templates: dict,
        global_ids: set,
        header_html: str | None,
        footer_html: str | None,
        company_name: str,
        lang: str,
    ) -> dict:
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
        return html_pages

    # ── Expozy publish ────────────────────────────────────────────────────────

    async def _publish_to_expozy(self, job, html_pages: dict):
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

    # ── Main process ──────────────────────────────────────────────────────────

    async def process(self, job_id, attempt: int) -> bool:
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
            site = await self.site_generator.generate(job.prompt_text)

            # ── Failure path ──────────────────────────────────────────────────
            if not site["success"]:
                if site["retryable"] and attempt < settings.max_retries:
                    await finish_job_attempt(
                        attempt_id,
                        AttemptOutcome.FAIL,
                        error_detail="; ".join(site["errors"][:5]),
                    )
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

                await finish_job_attempt(
                    attempt_id,
                    AttemptOutcome.FAIL,
                    error_detail="; ".join(site["errors"][:5]),
                )
                await update_job_status(
                    job_id,
                    JobStatus.FAILED,
                    error_message="; ".join(site["errors"][:3]),
                    raw_ai_response={"errors": site["errors"]},
                    validation_errors={"errors": site["errors"]},
                )
                return True

            # ── Success path ──────────────────────────────────────────────────
            templates = site["pages"]
            business_context = site.get("business_context", {})
            lang = business_context.get("primary_language", "bg")
            company_name = business_context.get("company_name", "EXPOZY Preview")

            catalog = get_catalog()
            global_ids = set(catalog.global_type_ids())

            html_pages = self._render_pages(
                templates=templates,
                global_ids=global_ids,
                header_html=templates.get("header"),
                footer_html=templates.get("footer"),
                company_name=company_name,
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

            await self._publish_to_expozy(job, html_pages)

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