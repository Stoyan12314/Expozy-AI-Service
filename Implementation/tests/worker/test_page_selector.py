"""
Tests for PageSelector.

The provider is stubbed so no real API calls are made.
"""

import pytest

from tests.worker.conftest import make_success_result, make_failure_result
from api.orchestrator.worker.service.page_selector import PageSelector


@pytest.fixture
def selector(provider, rag, catalog):
    return PageSelector(provider, rag, catalog)


@pytest.fixture
def ctx():
    return {"company_name": "Test", "business_type": "Café", "primary_language": "bg"}


@pytest.mark.asyncio
async def test_always_includes_global_types(selector, provider, catalog, ctx):
    # Arrange
    provider.set_result("page_selection", make_success_result("page_selection", template={"pages": ["homepage"]}))

    # Act
    result = await selector.select("A café", ctx, "bg")

    # Assert
    for gtype in catalog.global_type_ids():
        assert gtype in result


@pytest.mark.asyncio
async def test_removes_unknown_page_types(selector, provider, ctx):
    # Arrange
    provider.set_result(
        "page_selection",
        make_success_result("page_selection", template={"pages": ["homepage", "nonexistent_page_xyz"]}),
    )

    # Act
    result = await selector.select("A café", ctx, "bg")

    # Assert
    assert "nonexistent_page_xyz" not in result


@pytest.mark.asyncio
async def test_falls_back_to_globals_on_provider_failure(selector, provider, catalog, ctx):
    # Arrange
    provider.set_result("page_selection", make_failure_result("page_selection", errors=["Timeout"], retryable=False))

    # Act
    result = await selector.select("Something", ctx, "bg")

    # Assert
    for gtype in catalog.global_type_ids():
        assert gtype in result