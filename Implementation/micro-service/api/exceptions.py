"""
Exception handlers for the API.
"""

from fastapi import FastAPI, Request
from fastapi.responses import ORJSONResponse

from shared.utils import get_logger

logger = get_logger(__name__)


async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler - logs and returns generic error."""
    logger.error(
        "Unhandled exception",
        error=str(exc),
        path=request.url.path,
        method=request.method,
        exc_info=exc,
    )
    return ORJSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers with the app."""
    app.add_exception_handler(Exception, global_exception_handler)