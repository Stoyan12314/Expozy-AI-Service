"""
Preview Service - Secure Static File Server for Generated Templates.

Serves preview bundles from /previews directory with strict security headers.
This service MUST run on a separate domain/subdomain from the main application
to ensure complete cookie isolation and prevent any cross-origin attacks.

URL Format: /p/<bundle_id>/index.html

Security Features:
- Strict Content-Security-Policy blocking all scripts
- X-Content-Type-Options: nosniff
- No shared cookies with main app (separate domain)
- Frame ancestors blocked
- Form actions blocked

Environment Variables:
- PREVIEWS_PATH: Path to previews directory (default: /previews)
- PREVIEW_PORT: Port to listen on (default: 8001)
- LOG_LEVEL: Logging level (default: INFO)
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

from shared.utils import setup_logging, get_logger

# Initialize logging
setup_logging()
logger = get_logger(__name__)

# Configuration
PREVIEWS_PATH = Path(os.getenv("PREVIEWS_PATH", "/previews"))


# =============================================================================
# SECURITY HEADERS
# =============================================================================

# Strict Content-Security-Policy - blocks ALL scripts
SECURITY_HEADERS = {
    # Extremely restrictive CSP - no scripts, no external resources
    "Content-Security-Policy": (
        "default-src 'none'; "
        "script-src 'none'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'none'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none';"
    ),
    
    # Prevent MIME type sniffing
    "X-Content-Type-Options": "nosniff",
    
    # Don't send referrer
    "Referrer-Policy": "no-referrer",
    
    # Disable all permissions/features
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
    
    # Isolate browsing context
    "Cross-Origin-Opener-Policy": "same-origin",
    
    # Restrict resource sharing
    "Cross-Origin-Resource-Policy": "same-site",
    
    # Prevent embedding in frames (legacy browsers)
    "X-Frame-Options": "DENY",
    
    # XSS protection (legacy browsers)
    "X-XSS-Protection": "1; mode=block",
    
    # No caching of previews (they may be updated/deleted)
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware to add security headers to all responses.
    
    Applied to every response from the preview service.
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        
        # Add all security headers
        for header, value in SECURITY_HEADERS.items():
            response.headers[header] = value
        
        return response


# =============================================================================
# APPLICATION SETUP
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Ensure previews directory exists
    PREVIEWS_PATH.mkdir(parents=True, exist_ok=True)
    
    logger.info(
        "Preview service starting",
        previews_path=str(PREVIEWS_PATH),
        security_headers=list(SECURITY_HEADERS.keys()),
    )
    
    yield
    
    logger.info("Preview service shutting down")


app = FastAPI(
    title="Preview Service",
    description="Secure static file server for generated template previews",
    version="1.0.0",
    docs_url=None,      # Disable docs in production
    redoc_url=None,     # Disable redoc in production
    openapi_url=None,   # Disable OpenAPI in production
    lifespan=lifespan,
)

# Add security headers middleware
app.add_middleware(SecurityHeadersMiddleware)


# =============================================================================
# HEALTH CHECK
# =============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "preview",
        "previews_path": str(PREVIEWS_PATH),
        "previews_exist": PREVIEWS_PATH.exists(),
    }


# =============================================================================
# PREVIEW ROUTES
# =============================================================================

@app.get("/p/{bundle_id}/{file_path:path}")
async def serve_preview(bundle_id: str, file_path: str, request: Request) -> Response:
    """
    Serve preview files with strict security.
    
    URL format: /p/<bundle_id>/index.html
    
    Security checks:
    - Bundle ID must be valid UUID format
    - File path cannot contain ..
    - Only serves files from bundle directory
    """
    import re
    from uuid import UUID
    
    # Validate bundle_id is a valid UUID
    try:
        UUID(bundle_id)
    except ValueError:
        logger.warning("Invalid bundle_id format", bundle_id=bundle_id[:50])
        raise HTTPException(status_code=404, detail="Preview not found")
    
    # Default to index.html if no file specified
    if not file_path:
        file_path = "index.html"
    
    # Validate file_path - prevent directory traversal
    if ".." in file_path or file_path.startswith("/"):
        logger.warning(
            "Path traversal attempt blocked",
            bundle_id=bundle_id,
            file_path=file_path[:100],
        )
        raise HTTPException(status_code=404, detail="Invalid path")
    
    # Only allow safe characters in path
    if not re.match(r"^[a-zA-Z0-9_\-./]+$", file_path):
        logger.warning(
            "Invalid characters in path",
            bundle_id=bundle_id,
            file_path=file_path[:100],
        )
        raise HTTPException(status_code=404, detail="Invalid path")
    
    # Build full path
    bundle_path = PREVIEWS_PATH / bundle_id
    file_full_path = bundle_path / file_path
    
    # Ensure resolved path is within bundle directory (security check)
    try:
        file_full_path = file_full_path.resolve()
        bundle_path_resolved = bundle_path.resolve()
        
        if not str(file_full_path).startswith(str(bundle_path_resolved)):
            logger.warning(
                "Path escape attempt blocked",
                bundle_id=bundle_id,
                resolved_path=str(file_full_path),
            )
            raise HTTPException(status_code=404, detail="Invalid path")
    except Exception:
        raise HTTPException(status_code=404, detail="Preview not found")
    
    # Check if bundle exists
    if not bundle_path.exists():
        logger.debug("Bundle not found", bundle_id=bundle_id)
        raise HTTPException(status_code=404, detail="Preview not found")
    
    # Check if file exists
    if not file_full_path.exists() or not file_full_path.is_file():
        logger.debug(
            "File not found",
            bundle_id=bundle_id,
            file_path=file_path,
        )
        raise HTTPException(status_code=404, detail="File not found")
    
    # Determine content type
    content_type = get_content_type(file_path)
    
    # Log access
    logger.info(
        "Serving preview file",
        bundle_id=bundle_id,
        file_path=file_path,
        content_type=content_type,
        client_ip=request.client.host if request.client else "unknown",
    )
    
    # Return file with security headers (added by middleware)
    return FileResponse(
        path=file_full_path,
        media_type=content_type,
        filename=file_path.split("/")[-1],
    )


def get_content_type(file_path: str) -> str:
    """Get content type for file based on extension."""
    extension_map = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript",  # Should be blocked by CSP anyway
        ".json": "application/json",
        ".txt": "text/plain; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".ico": "image/x-icon",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
        ".eot": "application/vnd.ms-fontobject",
    }
    
    ext = "." + file_path.split(".")[-1].lower() if "." in file_path else ""
    return extension_map.get(ext, "application/octet-stream")


# =============================================================================
# SHORTHAND ROUTE - /p/<bundle_id>/ serves index.html
# =============================================================================

@app.get("/p/{bundle_id}")
@app.get("/p/{bundle_id}/")
async def serve_preview_index(bundle_id: str, request: Request) -> Response:
    """Redirect to index.html for bundle root requests."""
    return await serve_preview(bundle_id, "index.html", request)


# =============================================================================
# ROOT ROUTE - Informational
# =============================================================================

@app.get("/")
async def root():
    """Root endpoint - informational only."""
    return HTMLResponse(
        content="""
<!DOCTYPE html>
<html>
<head>
    <title>Preview Service</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { 
            font-family: system-ui, -apple-system, sans-serif; 
            max-width: 600px; 
            margin: 50px auto; 
            padding: 20px;
            color: #333;
            line-height: 1.6;
        }
        h1 { color: #1a1a1a; }
        code { 
            background: #f4f4f4; 
            padding: 2px 8px; 
            border-radius: 4px; 
            font-size: 0.9em;
        }
        .security {
            background: #e8f5e9;
            border-left: 4px solid #4caf50;
            padding: 12px 16px;
            margin: 20px 0;
            border-radius: 0 4px 4px 0;
        }
    </style>
</head>
<body>
    <h1>🔒 Preview Service</h1>
    <p>This service securely hosts generated template previews.</p>
    <p>Access previews at: <code>/p/{bundle_id}/index.html</code></p>
    <div class="security">
        <strong>Security:</strong> All responses include strict Content-Security-Policy 
        headers that block JavaScript execution.
    </div>
</body>
</html>
        """,
        status_code=200,
    )


# =============================================================================
# ERROR HANDLERS
# =============================================================================

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    """Custom 404 handler with security headers."""
    return HTMLResponse(
        content="""
<!DOCTYPE html>
<html>
<head>
    <title>404 - Not Found</title>
    <meta charset="utf-8">
    <style>
        body { 
            font-family: system-ui, sans-serif; 
            text-align: center; 
            padding: 50px;
            color: #333;
        }
        h1 { color: #e53e3e; font-size: 4rem; margin: 0; }
        p { color: #666; }
    </style>
</head>
<body>
    <h1>404</h1>
    <p>Preview not found or has expired.</p>
</body>
</html>
        """,
        status_code=404,
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler - logs and returns generic error."""
    logger.error(
        "Unhandled exception in preview service",
        error=str(exc),
        path=request.url.path,
        exc_info=exc,
    )
    return HTMLResponse(
        content="""
<!DOCTYPE html>
<html>
<head>
    <title>Error</title>
    <meta charset="utf-8">
    <style>
        body { 
            font-family: system-ui, sans-serif; 
            text-align: center; 
            padding: 50px;
            color: #333;
        }
        h1 { color: #e53e3e; }
    </style>
</head>
<body>
    <h1>Error</h1>
    <p>An error occurred while loading the preview.</p>
</body>
</html>
        """,
        status_code=500,
    )
