"""
Tests for PageGenerator.

The provider is stubbed so no real API calls are made.
asyncio.sleep is patched out so retry tests run instantly.
"""

import pytest
from unittest.mock import AsyncMock, patch

from tests.worker.conftest import make_success_result, make_failure_result
from api.orchestrator.worker.service.page_generator import PageGenerator


@pytest.fixture
def generator(provider, rag, catalog):
    return PageGenerator(provider, rag, catalog)


@pytest.fixture
def ctx():
    return {"company_name": "Test Co", "business_type": "Shop", "primary_language": "bg"}


@pytest.mark.asyncio
async def test_generate_returns_result_on_success(generator, provider, ctx):
    # Arrange
    provider.set_result("homepage", make_success_result("homepage", "<section>home</section>"))

    # Act
    result = await generator.generate(prompt="A café", page_type="homepage", business_context=ctx, lang="bg")

    # Assert
    assert result.success is True
    assert "<section>" in result.template


@pytest.mark.asyncio
async def test_retries_on_retryable_failure(generator, ctx):
    # Arrange
    fail = make_failure_result("homepage", errors=["STR-001 missing tag"], retryable=True)
    success = make_success_result("homepage")
    call_count = 0

    async def controlled_generate(**kwargs):
        nonlocal call_count
        call_count += 1
        return fail if call_count < 3 else success

    generator.generate = controlled_generate

    # Act
    with patch("api.orchestrator.worker.service.page_generator.asyncio.sleep", new=AsyncMock()):
        result = await generator.generate_with_retries(prompt="A café", page_type="homepage", business_context=ctx, lang="bg")

    # Assert
    assert result.success is True
    assert call_count == 3


def test_build_error_hints_matches_known_codes(generator):
    # Arrange
    hints_map = {"STR-001": "Use only allowed tags.", "ALP-020": "Invalid Alpine directive."}
    errors = ["STR-001 missing closing tag", "ALP-020 x-on:click not allowed", "UNKNOWN-999 ignore"]

    # Act
    output = generator._build_error_hints(errors, hints_map)

    # Assert
    assert "STR-001" in output
    assert "ALP-020" in output
    assert "UNKNOWN-999" not in output