"""
AI Registry — provider singleton.
"""
from api.orchestrator.ai.providers.providers.base import AlibabaCloudAdapter

try:
    from shared.utils.logging import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


_provider: AlibabaCloudAdapter | None = None


def get_provider() -> AlibabaCloudAdapter:
    global _provider
    if _provider is None:
        _provider = AlibabaCloudAdapter()
        logger.info("AI provider created", model=_provider.model)
    return _provider


def reset_provider() -> None:
    """For testing."""
    global _provider
    _provider = None