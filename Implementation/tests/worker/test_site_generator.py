"""
Tests for SiteGenerator.

The extractor, selector, and page generator are all mocked so only
the orchestration logic is tested.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from tests.worker.conftest import make_success_result, make_failure_result
from api.orchestrator.worker.service.site_generator import SiteGenerator


@pytest.fixture
def mock_extractor(sample_business_context):
    m = MagicMock()
    m.extract = AsyncMock(return_value=sample_business_context)
    return m


@pytest.fixture
def mock_selector():
    m = MagicMock()
    m.select = AsyncMock(return_value=["homepage", "header", "footer"])
    return m


@pytest.fixture
def mock_page_gen():
    m = MagicMock()
    m.generate_with_retries = AsyncMock(
        return_value=make_success_result("homepage", "<section>home</section>")
    )
    return m


@pytest.fixture
def site_generator(provider, rag, catalog, mock_extractor, mock_selector, mock_page_gen):
    gen = SiteGenerator.__new__(SiteGenerator)
    gen.provider = provider
    gen.rag = rag
    gen.catalog = catalog
    gen.extractor = mock_extractor
    gen.selector = mock_selector
    gen.page_generator = mock_page_gen
    return gen


@pytest.mark.asyncio
async def test_returns_success_with_all_pages(site_generator, mock_page_gen):
    # Arrange
    mock_page_gen.generate_with_retries = AsyncMock(
        side_effect=lambda prompt, page_type, **kwargs: make_success_result(page_type, f"<section>{page_type}</section>")
    )

    # Act
    result = await site_generator.generate("A café in Sofia")

    # Assert
    assert result["success"] is True
    assert "homepage" in result["pages"]
    assert result["pages"]["homepage"] is not None


@pytest.mark.asyncio
async def test_aborts_when_business_context_extraction_fails(site_generator, mock_extractor):
    # Arrange
    mock_extractor.extract = AsyncMock(return_value=None)

    # Act
    result = await site_generator.generate("Gibberish")

    # Assert
    assert result["success"] is False
    assert len(result["errors"]) > 0