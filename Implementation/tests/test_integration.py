"""
Integration tests for full end-to-end flow.

These tests require running services (docker-compose).
Run with: pytest tests/test_integration.py -v --integration

Tests:
- Send webhook -> job created -> worker processes -> preview file exists
- Full flow with real database and queue
"""

import os
import time
import pytest
import httpx
from uuid import UUID


# Skip integration tests unless explicitly requested
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS", "").lower() != "true",
    reason="Integration tests require running services. Set RUN_INTEGRATION_TESTS=true"
)


# Configuration from environment
API_URL = os.getenv("API_URL", "http://localhost:8000")
PREVIEW_URL = os.getenv("PREVIEW_URL", "http://localhost:8001")
WEBHOOK_SECRET = os.getenv("TELEGRAM_SECRET_TOKEN", "test-secret-token")


class TestIntegrationWebhookToPreview:
    """
    Integration tests for full webhook -> preview flow.
    
    Prerequisites:
        docker-compose up -d
        docker-compose run --rm migrations
    
    Run:
        RUN_INTEGRATION_TESTS=true pytest tests/test_integration.py -v
    """

    @pytest.fixture
    def unique_update_id(self):
        """Generate unique update_id for each test."""
        return int(time.time() * 1000000)

    def test_services_healthy(self):
        """Verify all services are running and healthy."""
        # Check API health
        response = httpx.get(f"{API_URL}/health", timeout=10)
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        
        # Check Preview health
        response = httpx.get(f"{PREVIEW_URL}/health", timeout=10)
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_webhook_creates_job(self, unique_update_id):
        """Test that webhook creates a job and returns job_id."""
        update_data = {
            "update_id": unique_update_id,
            "message": {
                "message_id": 1,
                "date": int(time.time()),
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 12345, "is_bot": False, "first_name": "IntegrationTest"},
                "text": "Create a simple landing page for testing",
            }
        }
        
        response = httpx.post(
            f"{API_URL}/telegram/webhook",
            json=update_data,
            headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
            timeout=30,
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "job_id" in data
        
        # Verify job_id is valid UUID
        job_id = data["job_id"]
        UUID(job_id)  # Raises if invalid

    def test_duplicate_webhook_returns_already_processing(self, unique_update_id):
        """Test that duplicate webhook returns existing job."""
        update_data = {
            "update_id": unique_update_id,
            "message": {
                "message_id": 1,
                "date": int(time.time()),
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 12345, "is_bot": False, "first_name": "Test"},
                "text": "Test duplicate handling",
            }
        }
        
        # First request
        response1 = httpx.post(
            f"{API_URL}/telegram/webhook",
            json=update_data,
            headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
            timeout=30,
        )
        assert response1.status_code == 200
        job_id_1 = response1.json().get("job_id")
        
        # Second request with same update_id
        response2 = httpx.post(
            f"{API_URL}/telegram/webhook",
            json=update_data,
            headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
            timeout=30,
        )
        assert response2.status_code == 200
        
        data2 = response2.json()
        assert "Already" in data2.get("message", "")
        
        # Should return same job_id
        if "job_id" in data2:
            assert data2["job_id"] == job_id_1

    def test_job_status_endpoint(self, unique_update_id):
        """Test job status retrieval after creation."""
        # Create job
        update_data = {
            "update_id": unique_update_id,
            "message": {
                "message_id": 1,
                "date": int(time.time()),
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 12345, "is_bot": False, "first_name": "Test"},
                "text": "Test job status endpoint",
            }
        }
        
        response = httpx.post(
            f"{API_URL}/telegram/webhook",
            json=update_data,
            headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
            timeout=30,
        )
        job_id = response.json()["job_id"]
        
        # Check job status
        response = httpx.get(f"{API_URL}/jobs/{job_id}/status", timeout=10)
        assert response.status_code == 200
        
        data = response.json()
        assert data["id"] == job_id
        assert data["status"] in ["queued", "running", "completed", "failed"]

    @pytest.mark.slow
    def test_full_flow_webhook_to_preview(self, unique_update_id):
        """
        Full integration test: webhook -> job -> worker -> preview.
        
        This test may take up to 60 seconds depending on AI provider.
        """
        # Step 1: Send webhook
        update_data = {
            "update_id": unique_update_id,
            "message": {
                "message_id": 1,
                "date": int(time.time()),
                "chat": {"id": 99999, "type": "private"},
                "from": {"id": 99999, "is_bot": False, "first_name": "FullTest"},
                "text": "Create a simple landing page with a hero section",
            }
        }
        
        response = httpx.post(
            f"{API_URL}/telegram/webhook",
            json=update_data,
            headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
            timeout=30,
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        
        # Step 2: Poll for completion (max 60 seconds)
        max_wait = 60
        poll_interval = 2
        waited = 0
        job_data = None
        
        while waited < max_wait:
            response = httpx.get(f"{API_URL}/jobs/{job_id}", timeout=10)
            assert response.status_code == 200
            job_data = response.json()
            
            if job_data["status"] in ["completed", "failed"]:
                break
            
            time.sleep(poll_interval)
            waited += poll_interval
        
        # Step 3: Verify completion
        assert job_data is not None
        assert job_data["status"] == "completed", f"Job failed: {job_data.get('error_message')}"
        assert job_data["bundle_id"] is not None
        assert job_data["preview_url"] is not None
        
        # Step 4: Verify preview is accessible
        preview_url = job_data["preview_url"]
        
        # Extract path from full URL or construct it
        if preview_url.startswith("http"):
            # Use as-is but replace host with our test preview URL
            bundle_id = job_data["bundle_id"]
            preview_path = f"/p/{bundle_id}/index.html"
        else:
            preview_path = preview_url
        
        response = httpx.get(f"{PREVIEW_URL}{preview_path}", timeout=10)
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        
        # Step 5: Verify security headers on preview
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert "script-src 'none'" in response.headers.get("Content-Security-Policy", "")

    def test_preview_404_for_invalid_bundle(self):
        """Test that preview returns 404 for non-existent bundle."""
        fake_bundle_id = "00000000-0000-0000-0000-000000000000"
        
        response = httpx.get(
            f"{PREVIEW_URL}/p/{fake_bundle_id}/index.html",
            timeout=10,
        )
        
        assert response.status_code == 404

    def test_preview_security_headers(self):
        """Test that preview service returns security headers."""
        # Even the root returns security headers
        response = httpx.get(f"{PREVIEW_URL}/", timeout=10)
        
        headers = response.headers
        
        # Check critical security headers
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert "frame-ancestors 'none'" in headers.get("Content-Security-Policy", "")
        assert headers.get("X-Frame-Options") == "DENY"


class TestIntegrationErrorCases:
    """Integration tests for error handling."""

    def test_invalid_webhook_secret_rejected(self):
        """Test that invalid secret token is rejected."""
        response = httpx.post(
            f"{API_URL}/telegram/webhook",
            json={"update_id": 1, "message": {}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
            timeout=10,
        )
        
        assert response.status_code == 401

    def test_malformed_webhook_rejected(self):
        """Test that malformed webhook payload is rejected."""
        response = httpx.post(
            f"{API_URL}/telegram/webhook",
            json={"invalid": "payload"},
            headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
            timeout=10,
        )
        
        # Should return 400 for invalid format
        assert response.status_code == 400

    def test_nonexistent_job_returns_404(self):
        """Test that fetching non-existent job returns 404."""
        fake_job_id = "00000000-0000-0000-0000-000000000000"
        
        response = httpx.get(f"{API_URL}/jobs/{fake_job_id}", timeout=10)
        
        assert response.status_code == 404


# =============================================================================
# HELPER FOR RUNNING INTEGRATION TESTS
# =============================================================================

def wait_for_services(timeout: int = 30):
    """Wait for services to be ready."""
    import time
    
    start = time.time()
    while time.time() - start < timeout:
        try:
            api_response = httpx.get(f"{API_URL}/health", timeout=5)
            preview_response = httpx.get(f"{PREVIEW_URL}/health", timeout=5)
            
            if api_response.status_code == 200 and preview_response.status_code == 200:
                return True
        except Exception:
            pass
        
        time.sleep(1)
    
    return False


if __name__ == "__main__":
    """Run integration tests directly."""
    print("Waiting for services...")
    if wait_for_services():
        print("Services ready, running tests...")
        pytest.main([__file__, "-v", "--tb=short"])
    else:
        print("Services not ready, please run: docker-compose up -d")
        exit(1)
