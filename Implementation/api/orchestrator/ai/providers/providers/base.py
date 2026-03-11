"""
 AI provider 

"""

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

try:
    from shared.utils.logging import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


# =============================================================================
# PAGE CONFIG — injected by template configs
# =============================================================================

@dataclass
class PageConfig:
    system_prompt: str
    validation_schema_path: str = ""
    response_schema: Optional[dict[str, Any]] = None
    max_tokens: int = 65,000
    temperature: float = 0.7
    validate_fn: Optional[Callable[[dict, str, str], dict]] = None
    expect_html: bool = False


# =============================================================================
# GENERATION RESULT
# =============================================================================

@dataclass
class GenerationResult:
    success: bool
    template: Optional[Any] = None
    raw_response: Optional[str] = None
    error: Optional[str] = None
    retryable: bool = False
    validation: Optional[dict[str, Any]] = None
    page_type: Optional[str] = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def all_errors(self) -> list[str]:
        if not self.validation:
            return [self.error] if self.error else []
        errors: list[str] = []
        # New HTML validator format (v4)
        errors.extend(self.validation.get("html_errors", []))
        errors.extend(self.validation.get("security_errors", []))
        errors.extend(self.validation.get("alpine_errors", []))
        errors.extend(self.validation.get("binding_errors", []))
        errors.extend(self.validation.get("field_errors", []))
        # Legacy JSON validator format (v1)
        errors.extend(self.validation.get("schema_errors", []))
        errors.extend(self.validation.get("semantic_errors", []))
        # Fallback
        if not errors and self.error:
            errors.append(self.error)
        return errors


# =============================================================================
# JSON REPAIR HELPERS
# =============================================================================

def _strip_markdown_json(text: str) -> str:
    """Remove ```json ... ``` fences if present."""
    stripped = text.strip()
    if stripped.startswith("```json"):
        stripped = stripped[7:]
    elif stripped.startswith("```"):
        stripped = stripped[3:]
    if stripped.endswith("```"):
        stripped = stripped[:-3]
    return stripped.strip()


def _try_parse_json(content: str) -> Any:
    """
    Attempt to parse JSON from LLM output, with progressive repair:
      1. Direct parse
      2. Strip markdown fences, then parse
      3. Use json_repair library (handles unescaped quotes, trailing commas, etc.)
    """
    # Pass 1: direct
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Pass 2: strip markdown fences
    cleaned = _strip_markdown_json(content)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Pass 3: json_repair library
    try:
        from json_repair import repair_json
        repaired = repair_json(cleaned, return_objects=False)
        result = json.loads(repaired)
        logger.info("JSON repaired successfully by json_repair")
        return result
    except Exception:
        pass

    # Pass 4: regex — fix common unescaped quotes in values like "CARTS" Ltd
    try:
        fixed = re.sub(
            r':\s*"([^"]*)"([^",}\]\n]+)',
            lambda m: f': "{m.group(1)}{m.group(2)}"',
            cleaned,
        )
        result = json.loads(fixed)
        logger.info("JSON repaired successfully by regex fix")
        return result
    except json.JSONDecodeError:
        pass

    # All repair attempts failed
    raise json.JSONDecodeError("All JSON repair attempts failed", content, 0)


# =============================================================================
# FIX 5: DETERMINISTIC HTML POST-PROCESSOR — runs BEFORE validation
# =============================================================================
# Fixes SEC-009: AI models frequently output <a target="_blank"> without
# rel="noopener". This is a deterministic fix — zero false positives.
# Runs on every HTML template before it reaches the validator.
# =============================================================================

_TARGET_BLANK_RE = re.compile(
    r'(<a\s[^>]*?)(target=["\']_blank["\'])([^>]*?>)', re.I
)


def _auto_fix_security(html: str) -> str:
    """
    Add rel="noopener noreferrer" to all <a target="_blank"> that lack it.
    Deterministic post-processor — zero false positives, eliminates SEC-009.
    """
    def _fix_match(m: re.Match) -> str:
        full = m.group(0)
        if "rel=" not in full.lower():
            return m.group(1) + m.group(2) + ' rel="noopener noreferrer"' + m.group(3)
        return full
    return _TARGET_BLANK_RE.sub(_fix_match, html)


# =============================================================================
# ALIBABA CLOUD ADAPTER
# =============================================================================

class AlibabaCloudAdapter:

    def __init__(self) -> None:
        self._api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        self._api_url = os.environ.get(
            "DASHSCOPE_API_URL",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
        )
        self._model = os.environ.get("AI_MODEL", "qwen-plus")
        self._timeout = int(os.environ.get("AI_TIMEOUT", "120"))

        if not self._api_key:
            logger.warning("DASHSCOPE_API_KEY not set — API calls will fail")

        logger.info(
            f"AI adapter initialised | model={self._model} | timeout={self._timeout}s"
        )

    @property
    def model(self) -> str:
        return self._model

    async def generate(
        self,
        prompt: str,
        page_type: str,
        page_config: PageConfig,
        lang: str = "en",
    ) -> GenerationResult:

        if not self._api_key:
            return GenerationResult(
                success=False, error="DASHSCOPE_API_KEY not set",
                retryable=False, page_type=page_type,
            )

        # ── 1. Build messages ──
        messages = [
            {"role": "system", "content": page_config.system_prompt},
            {"role": "user", "content": f"Generate the {page_type} page.\nUser request: {prompt}"},
        ]

        # ── 2. Build payload ──
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": page_config.temperature,
            "max_tokens": page_config.max_tokens,
        }

    
        if page_config.response_schema:
            payload["response_format"] = page_config.response_schema

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        # ── 3. Call API ──
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._api_url, headers=headers, json=payload
                )
            latency_ms = round((time.time() - t0) * 1000)

            if response.status_code == 429:
                return GenerationResult(
                    success=False, error="Rate limited", retryable=True,
                    page_type=page_type, latency_ms=latency_ms,
                )

            if response.status_code != 200:
                logger.error(
                    f"API error: {response.status_code} — {response.text[:500]}"
                )
                return GenerationResult(
                    success=False,
                    error=f"API error: {response.status_code}",
                    raw_response=response.text[:500],
                    retryable=response.status_code >= 500,
                    page_type=page_type, latency_ms=latency_ms,
                )

            try:
                resp_json = response.json()
            except Exception:
                return GenerationResult(
                    success=False, error="Non-JSON response",
                    raw_response=response.text[:500], retryable=True,
                    page_type=page_type, latency_ms=latency_ms,
                )

            if "error" in resp_json:
                err = resp_json["error"]
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                return GenerationResult(
                    success=False, error=msg, retryable=True,
                    page_type=page_type, latency_ms=latency_ms,
                )

            # ── 4. Extract content + usage ──
            content: str = resp_json["choices"][0]["message"]["content"]
            usage = resp_json.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)

            # ── 4b. Parse content (HTML or JSON) ──
            if page_config.expect_html:
                # Strip markdown code fences if model wraps HTML in ```html ... ```
                stripped = content.strip()
                if stripped.startswith("```html"):
                    stripped = stripped[7:]
                if stripped.startswith("```"):
                    stripped = stripped[3:]
                if stripped.endswith("```"):
                    stripped = stripped[:-3]
                template = stripped.strip()

                # ── FIX 5: Auto-fix security issues before validation ──
                # Deterministic post-processor: adds rel="noopener noreferrer"
                # to <a target="_blank"> links. Zero false positives.
                template = _auto_fix_security(template)
            else:
                try:
                    template = _try_parse_json(content)
                except json.JSONDecodeError as e:
                    logger.error(f"JSON parse failed (all repairs exhausted): {content[:300]}")
                    return GenerationResult(
                        success=False, error=f"Invalid JSON from model: {e}",
                        raw_response=content[:2000], retryable=True,
                        page_type=page_type, latency_ms=latency_ms,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                    )

            # ── 5. Validate (runs for BOTH HTML and JSON templates) ──
            validation: Optional[dict[str, Any]] = None
            if page_config.validate_fn:
                validation = page_config.validate_fn(
                    template, page_type, page_config.validation_schema_path
                )

                if not validation.get("accepted", False):
                    errors: list[str] = []
                    # New HTML validator format (v4)
                    errors.extend(validation.get("html_errors", []))
                    errors.extend(validation.get("security_errors", []))
                    errors.extend(validation.get("alpine_errors", []))
                    errors.extend(validation.get("binding_errors", []))
                    errors.extend(validation.get("field_errors", []))
                    # Legacy JSON validator format (v1)
                    errors.extend(validation.get("schema_errors", []))
                    errors.extend(validation.get("semantic_errors", []))

                    error_summary = "; ".join(errors[:5])
                    logger.warning(
                        f"Template rejected [{page_type}]: "
                        f"{validation.get('total_errors', len(errors))} errors — {error_summary}"
                    )

                    return GenerationResult(
                        success=False, template=template,
                        raw_response=content[:2000],
                        error=f"Validation failed: {error_summary}",
                        validation=validation, retryable=True,
                        page_type=page_type, latency_ms=latency_ms,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                    )

                # Log warnings (non-blocking)
                warnings = validation.get("warnings", [])
                if warnings:
                    logger.info(f"Template warnings [{page_type}]: {warnings[:5]}")

            # ── 6. Success ──
            return GenerationResult(
                success=True, template=template,
                raw_response=content[:2000], validation=validation,
                page_type=page_type, latency_ms=latency_ms,
                input_tokens=input_tokens, output_tokens=output_tokens,
            )

        except httpx.TimeoutException:
            return GenerationResult(
                success=False, error=f"Timeout ({self._timeout}s)",
                retryable=True, page_type=page_type,
                latency_ms=round((time.time() - t0) * 1000),
            )
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            return GenerationResult(
                success=False, error=str(e), retryable=True,
                page_type=page_type,
                latency_ms=round((time.time() - t0) * 1000),
            )