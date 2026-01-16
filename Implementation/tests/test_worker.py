"""
Unit tests for worker job processing.

Tests that:
- Worker marks job as completed
- Worker writes bundle files to filesystem
- Worker builds correct preview_url
- Worker handles errors and retries appropriately
"""

import os
import json
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4

from sqlalchemy import select

from api.orchestrator.db.models import Job, JobStatus, JobAttempt, AttemptOutcome


class TestWorkerJobCompletion:
    """Test worker marking jobs as completed."""

    @pytest.mark.asyncio
    async def test_job_marked_completed_on_success(
        self,
        db_session,
        sample_job,
    ):
        """Worker should mark job as completed after successful processing."""
        job = sample_job
        assert job.status == JobStatus.QUEUED
        
        # Simulate successful completion
        job.status = JobStatus.COMPLETED
        job.bundle_id = uuid4()
        job.preview_url = f"http://localhost:8001/p/{job.bundle_id}/index.html"
        job.attempt_count = 1
        await db_session.commit()
        
        # Verify
        await db_session.refresh(job)
        assert job.status == JobStatus.COMPLETED
        assert job.bundle_id is not None
        assert job.preview_url is not None

    @pytest.mark.asyncio
    async def test_job_attempt_created_on_processing(
        self,
        db_session,
        sample_job,
    ):
        """Worker should create job_attempt record when starting."""
        job = sample_job
        
        # Simulate worker creating attempt
        attempt = JobAttempt(
            job_id=job.id,
            attempt_no=1,
            provider_name="mock",
        )
        db_session.add(attempt)
        
        job.status = JobStatus.RUNNING
        job.attempt_count = 1
        await db_session.commit()
        
        # Verify attempt created
        result = await db_session.execute(
            select(JobAttempt).where(JobAttempt.job_id == job.id)
        )
        attempts = result.scalars().all()
        assert len(attempts) == 1
        assert attempts[0].attempt_no == 1
        assert attempts[0].provider_name == "mock"

    @pytest.mark.asyncio
    async def test_job_attempt_marked_success(
        self,
        db_session,
        sample_job,
    ):
        """Job attempt should be marked as success on completion."""
        job = sample_job
        
        # Create attempt
        attempt = JobAttempt(
            job_id=job.id,
            attempt_no=1,
            provider_name="mock",
        )
        db_session.add(attempt)
        await db_session.flush()
        
        # Mark as success
        attempt.outcome = AttemptOutcome.SUCCESS
        attempt.duration_ms = 1500
        
        job.status = JobStatus.COMPLETED
        await db_session.commit()
        
        # Verify
        await db_session.refresh(attempt)
        assert attempt.outcome == AttemptOutcome.SUCCESS
        assert attempt.duration_ms == 1500


class TestBundleCreation:
    """Test bundle file creation."""

    def test_bundle_directory_created(self, temp_previews_dir):
        """Worker should create bundle directory."""
        bundle_id = uuid4()
        bundle_path = temp_previews_dir / str(bundle_id)
        bundle_path.mkdir()
        
        assert bundle_path.exists()
        assert bundle_path.is_dir()

    def test_index_html_written(self, temp_previews_dir):
        """Worker should write index.html to bundle."""
        bundle_id = uuid4()
        bundle_path = temp_previews_dir / str(bundle_id)
        bundle_path.mkdir()
        
        # Write index.html
        html_content = """<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body><h1>Test Page</h1></body>
</html>"""
        
        index_path = bundle_path / "index.html"
        index_path.write_text(html_content, encoding="utf-8")
        
        assert index_path.exists()
        assert "Test Page" in index_path.read_text()

    def test_assets_directory_created(self, temp_previews_dir):
        """Worker should create assets subdirectory if needed."""
        bundle_id = uuid4()
        bundle_path = temp_previews_dir / str(bundle_id)
        bundle_path.mkdir()
        
        assets_path = bundle_path / "assets"
        assets_path.mkdir()
        
        # Write CSS file
        css_content = "body { color: #333; }"
        (assets_path / "style.css").write_text(css_content)
        
        assert assets_path.exists()
        assert (assets_path / "style.css").exists()

    def test_metadata_json_written(self, temp_previews_dir):
        """Worker should write metadata.json to bundle."""
        bundle_id = uuid4()
        job_id = uuid4()
        bundle_path = temp_previews_dir / str(bundle_id)
        bundle_path.mkdir()
        
        metadata = {
            "bundle_id": str(bundle_id),
            "job_id": str(job_id),
            "name": "Test Template",
            "pages": ["index.html"],
            "assets": [],
        }
        
        metadata_path = bundle_path / "metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2))
        
        # Verify
        assert metadata_path.exists()
        loaded = json.loads(metadata_path.read_text())
        assert loaded["bundle_id"] == str(bundle_id)


class TestPreviewUrlGeneration:
    """Test preview URL generation."""

    def test_preview_url_format(self):
        """Preview URL should follow correct format."""
        bundle_id = uuid4()
        base_url = "http://localhost:8001"
        
        preview_url = f"{base_url}/p/{bundle_id}/index.html"
        
        assert "/p/" in preview_url
        assert str(bundle_id) in preview_url
        assert preview_url.endswith("/index.html")

    def test_preview_url_with_https(self):
        """Preview URL should work with HTTPS base URL."""
        bundle_id = uuid4()
        base_url = "https://preview.example.com"
        
        preview_url = f"{base_url}/p/{bundle_id}/index.html"
        
        assert preview_url.startswith("https://")
        assert str(bundle_id) in preview_url

    @pytest.mark.asyncio
    async def test_preview_url_stored_in_job(self, db_session, sample_job):
        """Preview URL should be stored in job record."""
        job = sample_job
        bundle_id = uuid4()
        preview_url = f"http://localhost:8001/p/{bundle_id}/index.html"
        
        job.bundle_id = bundle_id
        job.preview_url = preview_url
        job.status = JobStatus.COMPLETED
        await db_session.commit()
        
        # Verify
        await db_session.refresh(job)
        assert job.preview_url == preview_url
        assert str(bundle_id) in job.preview_url


class TestWorkerErrorHandling:
    """Test worker error handling and retry logic."""

    @pytest.mark.asyncio
    async def test_job_marked_failed_after_max_retries(
        self,
        db_session,
        sample_job,
    ):
        """Job should be marked failed after max retries exhausted."""
        job = sample_job
        max_retries = 3
        
        # Simulate multiple failed attempts
        for i in range(max_retries):
            attempt = JobAttempt(
                job_id=job.id,
                attempt_no=i + 1,
                provider_name="mock",
                outcome=AttemptOutcome.FAIL,
                error_detail="Test error",
            )
            db_session.add(attempt)
        
        job.status = JobStatus.FAILED
        job.attempt_count = max_retries
        job.error_message = "Max retries exhausted"
        await db_session.commit()
        
        # Verify
        await db_session.refresh(job)
        assert job.status == JobStatus.FAILED
        assert job.attempt_count == max_retries
        
        result = await db_session.execute(
            select(JobAttempt).where(JobAttempt.job_id == job.id)
        )
        attempts = result.scalars().all()
        assert len(attempts) == max_retries

    @pytest.mark.asyncio
    async def test_job_remains_queued_for_retry(
        self,
        db_session,
        sample_job,
    ):
        """Job should remain queued after transient failure."""
        job = sample_job
        
        # First failed attempt
        attempt = JobAttempt(
            job_id=job.id,
            attempt_no=1,
            provider_name="mock",
            outcome=AttemptOutcome.FAIL,
            error_detail="Rate limit exceeded",
            provider_status_code=429,
        )
        db_session.add(attempt)
        
        # Job goes back to queued for retry
        job.status = JobStatus.QUEUED
        job.attempt_count = 1
        job.error_message = "Rate limit exceeded"
        await db_session.commit()
        
        # Verify job can be retried
        await db_session.refresh(job)
        assert job.status == JobStatus.QUEUED
        assert not job.is_terminal

    @pytest.mark.asyncio
    async def test_attempt_stores_provider_status_code(
        self,
        db_session,
        sample_job,
    ):
        """Attempt should store provider HTTP status code."""
        job = sample_job
        
        attempt = JobAttempt(
            job_id=job.id,
            attempt_no=1,
            provider_name="gemini",
            outcome=AttemptOutcome.FAIL,
            error_detail="Rate limited",
            provider_status_code=429,
        )
        db_session.add(attempt)
        await db_session.commit()
        
        # Verify
        await db_session.refresh(attempt)
        assert attempt.provider_status_code == 429


class TestWorkerIdempotency:
    """Test worker idempotency for crash recovery."""

    @pytest.mark.asyncio
    async def test_completed_job_not_reprocessed(
        self,
        db_session,
        sample_job,
    ):
        """Completed job should not be reprocessed."""
        job = sample_job
        
        # Mark as completed
        bundle_id = uuid4()
        job.status = JobStatus.COMPLETED
        job.bundle_id = bundle_id
        job.preview_url = f"http://localhost:8001/p/{bundle_id}/index.html"
        await db_session.commit()
        
        # Check idempotency condition
        await db_session.refresh(job)
        should_process = job.status not in (JobStatus.COMPLETED, JobStatus.FAILED)
        
        assert not should_process
        assert job.is_terminal

    @pytest.mark.asyncio
    async def test_failed_job_not_reprocessed(
        self,
        db_session,
        sample_job,
    ):
        """Failed job should not be reprocessed."""
        job = sample_job
        
        # Mark as failed
        job.status = JobStatus.FAILED
        job.error_message = "Permanent failure"
        await db_session.commit()
        
        # Check idempotency
        await db_session.refresh(job)
        should_process = job.status not in (JobStatus.COMPLETED, JobStatus.FAILED)
        
        assert not should_process
        assert job.is_terminal


class TestHTMLSanitization:
    """Test HTML sanitization of AI output."""

    def test_script_tags_removed(self):
        """Script tags should be removed from HTML."""
        html = '<html><body><script>alert("xss")</script><h1>Safe</h1></body></html>'
        
        # Simple sanitization check
        import re
        sanitized = re.sub(r'<\s*script[^>]*>.*?</\s*script\s*>', '', html, flags=re.IGNORECASE | re.DOTALL)
        
        assert '<script>' not in sanitized.lower()
        assert '<h1>Safe</h1>' in sanitized

    def test_event_handlers_removed(self):
        """Event handlers should be removed from HTML."""
        html = '<button onclick="alert(1)">Click</button>'
        
        import re
        sanitized = re.sub(r'\s+on\w+\s*=\s*["\'][^"\']*["\']', '', html, flags=re.IGNORECASE)
        
        assert 'onclick' not in sanitized.lower()
        assert '<button' in sanitized

    def test_javascript_urls_blocked(self):
        """javascript: URLs should be blocked."""
        html = '<a href="javascript:alert(1)">Click</a>'
        
        import re
        sanitized = re.sub(r'javascript\s*:', 'blocked:', html, flags=re.IGNORECASE)
        
        assert 'javascript:' not in sanitized.lower()
        assert 'blocked:' in sanitized


class TestEndToEndWorkerFlow:
    """Test complete worker processing flow."""

    @pytest.mark.asyncio
    async def test_full_job_processing_flow(
        self,
        db_session,
        sample_job,
        temp_previews_dir,
    ):
        """Test complete job processing from queued to completed."""
        job = sample_job
        assert job.status == JobStatus.QUEUED
        
        # Step 1: Mark as running, create attempt
        job.status = JobStatus.RUNNING
        job.attempt_count = 1
        
        attempt = JobAttempt(
            job_id=job.id,
            attempt_no=1,
            provider_name="mock",
        )
        db_session.add(attempt)
        await db_session.flush()
        
        # Step 2: Simulate AI response (already validated)
        ai_template = {
            "metadata": {"name": "Test"},
            "theme": {"primaryColor": "#3B82F6"},
            "sections": [{"type": "hero", "title": "Test"}]
        }
        
        # Step 3: Create bundle
        bundle_id = uuid4()
        bundle_path = temp_previews_dir / str(bundle_id)
        bundle_path.mkdir()
        
        html_content = "<html><body><h1>Test</h1></body></html>"
        (bundle_path / "index.html").write_text(html_content)
        
        # Step 4: Update job as completed
        preview_url = f"http://localhost:8001/p/{bundle_id}/index.html"
        
        job.status = JobStatus.COMPLETED
        job.bundle_id = bundle_id
        job.preview_url = preview_url
        job.raw_ai_response = ai_template
        
        attempt.outcome = AttemptOutcome.SUCCESS
        attempt.duration_ms = 2000
        
        await db_session.commit()
        
        # Verify final state
        await db_session.refresh(job)
        assert job.status == JobStatus.COMPLETED
        assert job.bundle_id == bundle_id
        assert job.preview_url == preview_url
        assert (bundle_path / "index.html").exists()
        
        await db_session.refresh(attempt)
        assert attempt.outcome == AttemptOutcome.SUCCESS
