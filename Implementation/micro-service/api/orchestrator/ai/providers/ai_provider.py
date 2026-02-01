"""
Pluggable AI provider adapter interface with comprehensive validation.

This version keeps ONLY VertexAIAdapter as the provider implementation.
"""

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Set

import httpx

from shared.config import get_settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# SYSTEM PROMPT FOR AI PROVIDERS
# =============================================================================

SYSTEM_PROMPT = """You are an EXPOZY template generator. Generate JSON template packages for e-commerce pages.

CRITICAL ENDPOINT FORMAT RULES:
All endpoints in dataSources and actions MUST follow one of these exact formats:

1. API Format: verb.resource
   - verb: get, post, put, patch, or delete (lowercase)
   - resource: lowercase with underscores
   - Examples: get.products, get.testimonials, post.contact, post.newsletter

2. Module Format: Module.method
   - Module: PascalCase (Shop, Blog, User, Auth, Newsletter, Contact)
   - method: lowercase with underscores
   - Examples: Shop.post_carts, Shop.get_wishlist, Newsletter.subscribe, Auth.login

NEVER use URL paths like /api/products or /api/v1/posts. ALWAYS use dot notation.

Common DataSource endpoints: get.products, get.categories, get.posts, get.testimonials, get.reviews, get.faqs, get.user, get.orders, Shop.get_cart, Blog.get_posts
Common Action endpoints: post.contact, post.newsletter, Shop.post_carts, Shop.post_wishlist, Shop.post_checkout, Auth.login, Auth.register, Contact.submit

Return a JSON object with this structure:
{
  "metadata": {"name": "Template Name", "description": "Brief description", "pageType": "landing", "route": "/page"},
  "theme": {"primaryColor": "#3B82F6", "darkMode": false},
  "dataSources": [{"id": "source_id", "endpoint": "get.products", "params": {"limit": 12}}],
  "actions": [{"id": "action_id", "endpoint": "Shop.post_carts", "method": "POST"}],
  "sections": [{"type": "hero", "title": "Title", "buttons": [{"label": "Click", "variant": "primary"}]}]
}

RULES:
1. Always include at least one section
2. Use only valid endpoint formats (verb.resource or Module.method)
3. Ensure all actionRef and dataSource references match declared IDs
4. Use only safe content - no script tags, event handlers, or javascript: URLs
5. Return ONLY valid JSON, no markdown"""


# =============================================================================
# VALIDATION PATTERNS
# =============================================================================

API_PATTERN = re.compile(r"^(get|post|put|patch|delete)\.[a-z][a-z0-9_]*$")
MODULE_PATTERN = re.compile(r"^[A-Z][A-Za-z0-9_]*\.[a-z][a-z0-9_]*$")
DANGEROUS_ENDPOINTS = re.compile(
    r"(drop|truncate|delete\.users|delete\.all|admin|exec|eval|system)",
    re.IGNORECASE
)

UNSAFE_PATTERNS = [
    (re.compile(r"<\s*script\b", re.IGNORECASE), "script tag"),
    (re.compile(r"javascript\s*:", re.IGNORECASE), "javascript: protocol"),
    (re.compile(r"\bon\w+\s*=", re.IGNORECASE), "inline event handler (on*=)"),
    (re.compile(r"<\s*iframe\b", re.IGNORECASE), "iframe tag"),
    (re.compile(r"<\s*object\b", re.IGNORECASE), "object tag"),
    (re.compile(r"<\s*embed\b", re.IGNORECASE), "embed tag"),
    (re.compile(r"<\s*form\b[^>]*action\s*=", re.IGNORECASE), "form with action"),
    (re.compile(r"<\s*meta\b[^>]*http-equiv", re.IGNORECASE), "meta http-equiv"),
    (re.compile(r"<\s*link\b[^>]*rel\s*=\s*['\"]?import", re.IGNORECASE), "link import"),
    (re.compile(r"<\s*base\b", re.IGNORECASE), "base tag"),
    (re.compile(r"expression\s*\(", re.IGNORECASE), "CSS expression()"),
    (re.compile(r"url\s*\(\s*['\"]?\s*data:", re.IGNORECASE), "data: URL"),
    (re.compile(r"@import\s+", re.IGNORECASE), "CSS @import"),
]

ALPINE_UNSAFE_PATTERNS = [
    (re.compile(r"x-html\s*=", re.IGNORECASE), "x-html directive (allows raw HTML injection)"),
    (re.compile(r"x-on\s*:\s*\w+\s*=\s*['\"][^'\"]*\(", re.IGNORECASE), "x-on with function call"),
    (re.compile(r"@\w+\s*=\s*['\"][^'\"]*\(", re.IGNORECASE), "@ shorthand with function call"),
    (re.compile(r"x-init\s*=\s*['\"][^'\"]*fetch\s*\(", re.IGNORECASE), "x-init with fetch"),
    (re.compile(r"x-init\s*=\s*['\"][^'\"]*eval\s*\(", re.IGNORECASE), "x-init with eval"),
    (re.compile(r"\$refs\s*\[", re.IGNORECASE), "$refs bracket access"),
    (re.compile(r"\$el\s*\.", re.IGNORECASE), "$el direct manipulation"),
    (re.compile(r"x-data\s*=\s*['\"]?\s*\{[^}]{500,}", re.IGNORECASE), "x-data with large object (>500 chars)"),
]

TAILWIND_DANGEROUS_PATTERNS = [
    (re.compile(r"\[\s*['\"].*<", re.IGNORECASE), "arbitrary content with HTML"),
    (re.compile(r"\[\s*['\"].*javascript:", re.IGNORECASE), "arbitrary content with javascript:"),
    (re.compile(r"content-\[[^\]]*<", re.IGNORECASE), "content-[] with HTML"),
    (re.compile(r"url\([^)]*\)", re.IGNORECASE), "url() in arbitrary value"),
]

HEX_COLOR_3 = re.compile(r"^#[0-9a-fA-F]{3}$")
HEX_COLOR_6 = re.compile(r"^#[0-9a-fA-F]{6}$")
HEX_COLOR_8 = re.compile(r"^#[0-9a-fA-F]{8}$")

ROUTE_VALID = re.compile(r"^/[a-zA-Z0-9_\-/{}]*$")
ROUTE_DANGEROUS = [
    (re.compile(r"<\s*script", re.IGNORECASE), "script tag in route"),
    (re.compile(r"javascript:", re.IGNORECASE), "javascript: in route"),
    (re.compile(r"\.\./"), "directory traversal (..)"),
    (re.compile(r"[<>\"']"), "special characters"),
]

MAX_CLASSNAME_LENGTH = 500

PAGE_TYPE_REQUIREMENTS = {
    "product": {
        "recommended_params": ["product_id", "slug"],
        "recommended_sections": ["products", "content"],
        "recommended_actions": ["Shop.post_carts", "Shop.post_wishlist"],
    },
    "category": {
        "recommended_params": ["category_id", "limit", "page"],
        "recommended_sections": ["products"],
        "optional_params": ["sort", "order", "filter", "search"],
    },
    "blog": {
        "recommended_sections": ["posts", "content"],
        "recommended_endpoints": ["get.posts"],
    },
    "cart": {
        "recommended_sections": ["content", "cta"],
        "recommended_actions": ["Shop.post_carts", "Shop.delete_carts"],
    },
    "account": {
        "recommended_sections": ["form", "content"],
        "recommended_endpoints": ["get.user", "get.orders"],
    },
    "contact": {
        "recommended_sections": ["form", "content"],
        "required_form_fields": ["name", "email", "message"],
    },
    "landing": {
        "recommended_sections": ["hero", "features", "cta"],
    },
}


# =============================================================================
# VALIDATION RESULT
# =============================================================================

@dataclass
class ValidationResult:
    valid: bool = True
    endpoint_errors: list[str] = field(default_factory=list)
    crossref_errors: list[str] = field(default_factory=list)
    security_flags: list[str] = field(default_factory=list)
    alpine_errors: list[str] = field(default_factory=list)
    tailwind_errors: list[str] = field(default_factory=list)
    theme_errors: list[str] = field(default_factory=list)
    route_errors: list[str] = field(default_factory=list)
    page_type_warnings: list[str] = field(default_factory=list)
    completeness_warnings: list[str] = field(default_factory=list)

    def has_errors(self) -> bool:
        return bool(
            self.endpoint_errors
            or self.crossref_errors
            or self.security_flags
            or self.alpine_errors
            or self.tailwind_errors
            or self.theme_errors
            or self.route_errors
        )

    def has_warnings(self) -> bool:
        return bool(self.page_type_warnings or self.completeness_warnings)

    def all_errors(self) -> list[str]:
        return (
            self.endpoint_errors
            + self.crossref_errors
            + self.security_flags
            + self.alpine_errors
            + self.tailwind_errors
            + self.theme_errors
            + self.route_errors
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "has_errors": self.has_errors(),
            "has_warnings": self.has_warnings(),
            "endpoint_errors": self.endpoint_errors,
            "crossref_errors": self.crossref_errors,
            "security_flags": self.security_flags,
            "alpine_errors": self.alpine_errors,
            "tailwind_errors": self.tailwind_errors,
            "theme_errors": self.theme_errors,
            "route_errors": self.route_errors,
            "page_type_warnings": self.page_type_warnings,
            "completeness_warnings": self.completeness_warnings,
        }


@dataclass
class GenerationResult:
    success: bool
    template: Optional[dict[str, Any]] = None
    raw_response: Optional[str] = None
    error: Optional[str] = None
    retryable: bool = False
    validation: Optional[ValidationResult] = None


# =============================================================================
# TEMPLATE VALIDATOR
# =============================================================================

class TemplateValidator:
    @staticmethod
    def validate_endpoints(pkg: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        for i, ds in enumerate(pkg.get("dataSources") or []):
            if isinstance(ds, dict):
                ep = ds.get("endpoint", "")
                if ep and not (API_PATTERN.match(ep) or MODULE_PATTERN.match(ep)):
                    errors.append(f"dataSources[{i}].endpoint '{ep}' invalid format")
                if ep and DANGEROUS_ENDPOINTS.search(ep):
                    errors.append(f"dataSources[{i}].endpoint '{ep}' matches dangerous pattern")
        for i, ac in enumerate(pkg.get("actions") or []):
            if isinstance(ac, dict):
                ep = ac.get("endpoint", "")
                if ep and not (API_PATTERN.match(ep) or MODULE_PATTERN.match(ep)):
                    errors.append(f"actions[{i}].endpoint '{ep}' invalid format")
                if ep and DANGEROUS_ENDPOINTS.search(ep):
                    errors.append(f"actions[{i}].endpoint '{ep}' matches dangerous pattern")
        return errors

    @staticmethod
    def validate_crossrefs(pkg: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        ds_ids: Set[str] = {d.get("id") for d in (pkg.get("dataSources") or []) if isinstance(d, dict) and d.get("id")}
        ac_ids: Set[str] = {a.get("id") for a in (pkg.get("actions") or []) if isinstance(a, dict) and a.get("id")}
        for i, sec in enumerate(pkg.get("sections", []) or []):
            if not isinstance(sec, dict):
                continue
            ds = sec.get("dataSource")
            if ds and ds not in ds_ids:
                errors.append(f"sections[{i}].dataSource '{ds}' references unknown id")
            ar = sec.get("actionRef")
            if ar and ar not in ac_ids:
                errors.append(f"sections[{i}].actionRef '{ar}' references unknown id")
            for j, btn in enumerate(sec.get("buttons") or []):
                if isinstance(btn, dict):
                    btn_ar = btn.get("actionRef")
                    if btn_ar and btn_ar not in ac_ids:
                        errors.append(f"sections[{i}].buttons[{j}].actionRef '{btn_ar}' references unknown id")
        return errors

    @staticmethod
    def find_unsafe_strings(obj: Any, path: str = "$") -> list[str]:
        hits: list[str] = []
        if isinstance(obj, str):
            for pat, desc in UNSAFE_PATTERNS:
                if pat.search(obj):
                    hits.append(f"{path}: contains {desc}")
            return hits
        if isinstance(obj, dict):
            for k, v in obj.items():
                hits.extend(TemplateValidator.find_unsafe_strings(v, f"{path}.{k}"))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                hits.extend(TemplateValidator.find_unsafe_strings(v, f"{path}[{i}]"))
        return hits

    @staticmethod
    def validate_alpine(pkg: dict[str, Any]) -> list[str]:
        errors: list[str] = []

        def check_string(val: str, path: str) -> None:
            for pat, desc in ALPINE_UNSAFE_PATTERNS:
                if pat.search(val):
                    errors.append(f"{path}: contains unsafe Alpine pattern - {desc}")

        def recurse(obj: Any, path: str) -> None:
            if isinstance(obj, str):
                check_string(obj, path)
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    recurse(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    recurse(v, f"{path}[{i}]")

        recurse(pkg, "$")
        return errors

    @staticmethod
    def validate_tailwind(pkg: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        for i, sec in enumerate(pkg.get("sections", []) or []):
            if not isinstance(sec, dict):
                continue
            cn = sec.get("className")
            if not cn or not isinstance(cn, str):
                continue
            path = f"sections[{i}].className"
            if len(cn) > MAX_CLASSNAME_LENGTH:
                errors.append(f"{path}: exceeds max length ({len(cn)} > {MAX_CLASSNAME_LENGTH})")
            for pat, desc in TAILWIND_DANGEROUS_PATTERNS:
                if pat.search(cn):
                    errors.append(f"{path}: contains dangerous pattern - {desc}")
        return errors

    @staticmethod
    def validate_theme(pkg: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        theme = pkg.get("theme")
        if not theme or not isinstance(theme, dict):
            return []
        color = theme.get("primaryColor")
        if color:
            if not isinstance(color, str):
                errors.append(f"theme.primaryColor: expected string, got {type(color).__name__}")
            elif not (HEX_COLOR_3.match(color) or HEX_COLOR_6.match(color) or HEX_COLOR_8.match(color)):
                css_named = {"black", "white", "red", "green", "blue", "yellow", "transparent"}
                if color.lower() not in css_named and not color.startswith("rgb") and not color.startswith("hsl"):
                    errors.append(f"theme.primaryColor '{color}' is not a valid hex color format")
        dark_mode = theme.get("darkMode")
        if dark_mode is not None and not isinstance(dark_mode, bool):
            errors.append(f"theme.darkMode: expected boolean, got {type(dark_mode).__name__}")
        return errors

    @staticmethod
    def validate_route(pkg: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        metadata = pkg.get("metadata", {})
        route = metadata.get("route")
        if not route:
            return []
        if not isinstance(route, str):
            return [f"metadata.route: expected string, got {type(route).__name__}"]
        for pat, desc in ROUTE_DANGEROUS:
            if pat.search(route):
                errors.append(f"metadata.route '{route}' contains {desc}")
        if not ROUTE_VALID.match(route):
            if "?" in route:
                errors.append(f"metadata.route '{route}' contains query string - handle separately")
            elif not route.startswith("/"):
                errors.append(f"metadata.route '{route}' must start with '/'")
        return errors

    @staticmethod
    def validate_page_type(pkg: dict[str, Any]) -> list[str]:
        warnings: list[str] = []
        metadata = pkg.get("metadata", {})
        page_type = metadata.get("pageType")
        if not page_type or page_type not in PAGE_TYPE_REQUIREMENTS:
            return []
        reqs = PAGE_TYPE_REQUIREMENTS[page_type]
        section_types: Set[str] = set()
        form_fields: Set[str] = set()
        for sec in (pkg.get("sections") or []):
            if isinstance(sec, dict):
                section_types.add(sec.get("type", ""))
                for fld in (sec.get("fields") or []):
                    if isinstance(fld, dict):
                        form_fields.add(fld.get("name", ""))
        if "recommended_sections" in reqs:
            for sec_type in reqs["recommended_sections"]:
                if sec_type not in section_types:
                    warnings.append(f"pageType '{page_type}' typically includes section type '{sec_type}'")
        if "required_form_fields" in reqs:
            for field_name in reqs["required_form_fields"]:
                if field_name not in form_fields:
                    warnings.append(f"pageType '{page_type}' typically requires form field '{field_name}'")
        return warnings

    @staticmethod
    def validate_completeness(pkg: dict[str, Any]) -> list[str]:
        warnings: list[str] = []
        sections = pkg.get("sections", [])
        if not sections:
            warnings.append("sections array is empty - page has no content")
            return warnings
        for i, sec in enumerate(sections):
            if isinstance(sec, dict) and sec.get("type") == "form":
                if not sec.get("fields", []):
                    warnings.append(f"sections[{i}]: form section has no fields defined")
                if not sec.get("actionRef"):
                    warnings.append(f"sections[{i}]: form section has no actionRef - form won't submit")
        for i, sec in enumerate(sections):
            if isinstance(sec, dict):
                sec_type = sec.get("type")
                if sec_type in ("products", "posts") and not sec.get("dataSource"):
                    warnings.append(f"sections[{i}]: '{sec_type}' section has no dataSource")
        return warnings

    @classmethod
    def validate(cls, pkg: dict[str, Any]) -> ValidationResult:
        result = ValidationResult()
        result.endpoint_errors = cls.validate_endpoints(pkg)
        result.crossref_errors = cls.validate_crossrefs(pkg)
        result.security_flags = cls.find_unsafe_strings(pkg)
        result.alpine_errors = cls.validate_alpine(pkg)
        result.tailwind_errors = cls.validate_tailwind(pkg)
        result.theme_errors = cls.validate_theme(pkg)
        result.route_errors = cls.validate_route(pkg)
        result.page_type_warnings = cls.validate_page_type(pkg)
        result.completeness_warnings = cls.validate_completeness(pkg)
        result.valid = not result.has_errors()
        return result


# =============================================================================
# PROVIDER ADAPTER INTERFACE
# =============================================================================

class ProviderAdapter(ABC):
    @abstractmethod
    async def generate(self, prompt: str) -> GenerationResult:
        raise NotImplementedError

    def _validate_template(self, template: dict[str, Any]) -> ValidationResult:
        return TemplateValidator.validate(template)


def load_response_schema() -> Optional[dict[str, Any]]:
    schema_path = Path(__file__).parent / "expozy_schemaV2.json"
    if schema_path.exists():
        try:
            with open(schema_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load schema: {e}")
    return None


# =============================================================================
# VERTEX AI ADAPTER (Google Cloud)
# =============================================================================

class VertexAIAdapter(ProviderAdapter):
    """Google Cloud Vertex AI adapter with OAuth2 service-account auth."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._response_schema = load_response_schema()
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0
        self._credentials = self._load_credentials()

        if self._response_schema:
            logger.info("Loaded EXPOZY response schema for structured output")

    def _load_credentials(self) -> Optional[dict[str, Any]]:
        creds_json = self._settings.vertex_service_account_json
        if creds_json:
            try:
                creds = json.loads(creds_json)
                logger.info(
                    "Loaded Vertex AI service account credentials",
                    client_email=creds.get("client_email", "unknown"),
                )
                return creds
            except json.JSONDecodeError as e:
                logger.error("Failed to parse VERTEX_SERVICE_ACCOUNT_JSON", error=str(e))
        else:
            logger.warning("VERTEX_SERVICE_ACCOUNT_JSON not set")
        return None

    async def _get_access_token(self) -> str:
        # Import inside method so your app fails only if you actually use Vertex.
        import jwt  # requires PyJWT

        if self._access_token and time.time() < self._token_expiry - 60:
            return self._access_token

        if not self._credentials:
            raise ValueError("No service account credentials available")

        now = int(time.time())
        payload = {
            "iss": self._credentials["client_email"],
            "sub": self._credentials["client_email"],
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
            "scope": "https://www.googleapis.com/auth/cloud-platform",
        }

        signed_jwt = jwt.encode(payload, self._credentials["private_key"], algorithm="RS256")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": signed_jwt,
                },
            )
            if response.status_code != 200:
                logger.error(
                    "Failed to get access token",
                    status=response.status_code,
                    response=response.text[:500],
                )
                raise ValueError(f"Token exchange failed: {response.status_code}")

            token_data = response.json()
            self._access_token = token_data["access_token"]
            self._token_expiry = now + token_data.get("expires_in", 3600)
            logger.debug("Obtained new access token", expires_in=token_data.get("expires_in"))
            return self._access_token

    async def generate(self, prompt: str) -> GenerationResult:
        project_id = self._settings.vertex_project_id
        region = self._settings.vertex_region or "europe-west1"
        model = self._settings.ai_model or "gemini-2.0-flash-001"

        if not project_id:
            return GenerationResult(success=False, error="VERTEX_PROJECT_ID not configured", retryable=False)
        if not self._credentials:
            return GenerationResult(success=False, error="VERTEX_SERVICE_ACCOUNT_JSON not configured", retryable=False)

        url = (
            f"https://{region}-aiplatform.googleapis.com/v1/"
            f"projects/{project_id}/locations/{region}/"
            f"publishers/google/models/{model}:generateContent"
        )

        try:
            access_token = await self._get_access_token()
        except Exception as e:
            logger.error("Failed to get access token", error=str(e))
            return GenerationResult(success=False, error=f"Authentication failed: {e}", retryable=True)

        generation_config: dict[str, Any] = {
            "temperature": 0.2,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
        }
        if self._response_schema:
            generation_config["responseSchema"] = self._response_schema

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"{SYSTEM_PROMPT}\n\nUser request: {prompt}"}],
                }
            ],
            "generationConfig": generation_config,
        }
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=self._settings.ai_timeout) as client:
                response = await client.post(url, headers=headers, json=payload)

                if response.status_code == 429:
                    return GenerationResult(success=False, error="Rate limited", retryable=True)

                if response.status_code == 401:
                    # force refresh token next time
                    self._access_token = None
                    self._token_expiry = 0
                    return GenerationResult(success=False, error="Authentication expired", retryable=True)

                if response.status_code != 200:
                    logger.error(
                        "Vertex AI API error",
                        status_code=response.status_code,
                        response=response.text[:500],
                    )
                    return GenerationResult(
                        success=False,
                        error=f"API error: {response.status_code}",
                        raw_response=response.text[:500],
                        retryable=response.status_code >= 500,
                    )

                data = response.json()
                try:
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError) as e:
                    logger.error("Failed to extract text from Vertex AI response", error=str(e))
                    return GenerationResult(
                        success=False,
                        error="Invalid response structure",
                        raw_response=json.dumps(data)[:500],
                        retryable=True,
                    )

                try:
                    template = json.loads(text)
                except json.JSONDecodeError as e:
                    logger.error("Failed to parse JSON from Vertex AI", error=str(e), text=text[:500])
                    return GenerationResult(
                        success=False,
                        error=f"Invalid JSON: {e}",
                        raw_response=text[:500],
                        retryable=True,
                    )

                validation = self._validate_template(template)
                if validation.has_errors():
                    logger.warning("Template validation errors", errors=validation.all_errors())
                    return GenerationResult(
                        success=False,
                        template=template,
                        raw_response=text[:2000],
                        error=f"Validation failed: {'; '.join(validation.all_errors()[:3])}",
                        validation=validation,
                        retryable=True,
                    )

                if validation.has_warnings():
                    logger.info(
                        "Template validation warnings",
                        warnings=validation.page_type_warnings + validation.completeness_warnings,
                    )

                return GenerationResult(success=True, template=template, raw_response=text[:2000], validation=validation)

        except httpx.TimeoutException:
            logger.error("Vertex AI API timeout", timeout=self._settings.ai_timeout)
            return GenerationResult(success=False, error="Timeout", retryable=True)
        except Exception as e:
            logger.error("Unexpected error in Vertex AI adapter", error=str(e), exc_info=True)
            return GenerationResult(success=False, error=str(e), retryable=True)


# =============================================================================
# PROVIDER FACTORY (Vertex only)
# =============================================================================

def get_provider() -> ProviderAdapter:
    """
    Always return VertexAIAdapter.

    Make sure your env has:
      - VERTEX_PROJECT_ID
      - VERTEX_REGION (optional)
      - VERTEX_SERVICE_ACCOUNT_JSON
      - AI_MODEL (optional)
    """
    settings = get_settings()
    logger.info(
        "Using Vertex AI provider",
        project=settings.vertex_project_id,
        region=settings.vertex_region,
        model=settings.ai_model,
    )
    return VertexAIAdapter()
