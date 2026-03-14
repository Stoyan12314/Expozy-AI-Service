"""
Tests for BusinessContextExtractor (Step 1 of the pipeline).

The AI provider is replaced with a stub so no real API calls are made.
"""

import json
import pytest

from api.orchestrator.ai.providers.providers.base import GenerationResult
from api.orchestrator.worker.service.business_context_extractor import BusinessContextExtractor
from tests.worker.conftest import make_success_result, make_failure_result


@pytest.fixture
def extractor(provider, rag, catalog):
    return BusinessContextExtractor(provider, rag, catalog)


@pytest.mark.asyncio
async def test_returns_dict_on_success(extractor, provider):
    # Arrange
    expected = {"company_name": "Café Roma", "business_type": "Restaurant"}
    provider.set_result("context_extraction", make_success_result("context_extraction", template=expected))

    # Act
    result = await extractor.extract("A café in Sofia", "bg")

    # Assert
    assert result == expected


@pytest.mark.asyncio
async def test_returns_none_when_provider_fails(extractor, provider):
    # Arrange
    provider.set_result("context_extraction", make_failure_result("context_extraction", errors=["Timeout"]))

    # Act
    result = await extractor.extract("A random prompt", "bg")

    # Assert
    assert result is None