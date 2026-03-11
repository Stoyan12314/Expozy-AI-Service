"""
HTML sanitization service for AI-generated content.

Treats all AI output as untrusted and sanitizes it before storage/display.
Uses bleach for HTML sanitization with strict allowlists.
"""

import re
from typing import Any, Optional

import bleach
from bleach.css_sanitizer import CSSSanitizer

from shared.utils.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# ALLOWED HTML ELEMENTS AND ATTRIBUTES
# =============================================================================

# Safe HTML tags for template content
ALLOWED_TAGS = frozenset([
    # Structure
    "div", "span", "section", "article", "header", "footer", "nav", "main", "aside",
    # Text
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr",
    # Formatting
    "strong", "b", "em", "i", "u", "s", "small", "mark", "sub", "sup",
    # Lists
    "ul", "ol", "li", "dl", "dt", "dd",
    # Links and media (href/src sanitized separately)
    "a", "img",
    # Tables
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
    # Forms (limited)
    "form", "input", "button", "label", "select", "option", "textarea",
    # Other
    "blockquote", "pre", "code", "figure", "figcaption", "time",
])

# Safe attributes per tag
ALLOWED_ATTRIBUTES = {
    "*": ["class", "id", "style", "data-*", "aria-*", "role", "title"],
    "a": ["href", "target", "rel"],
    "img": ["src", "alt", "width", "height", "loading"],
    "input": ["type", "name", "value", "placeholder", "required", "disabled", "readonly", "checked"],
    "button": ["type", "name", "value", "disabled"],
    "label": ["for"],
    "select": ["name", "required", "disabled", "multiple"],
    "option": ["value", "selected", "disabled"],
    "textarea": ["name", "placeholder", "required", "disabled", "readonly", "rows", "cols"],
    "form": ["action", "method", "enctype"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan", "scope"],
    "time": ["datetime"],
}

# Safe CSS properties for inline styles
ALLOWED_CSS_PROPERTIES = frozenset([
    # Layout
    "display", "visibility", "position", "top", "right", "bottom", "left",
    "float", "clear", "overflow", "z-index",
    # Flexbox
    "flex", "flex-direction", "flex-wrap", "justify-content", "align-items", "align-content",
    "flex-grow", "flex-shrink", "flex-basis", "align-self", "order", "gap",
    # Grid
    "grid", "grid-template-columns", "grid-template-rows", "grid-gap",
    # Box model
    "width", "height", "min-width", "max-width", "min-height", "max-height",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "border", "border-width", "border-style", "border-color", "border-radius",
    # Typography
    "font", "font-family", "font-size", "font-weight", "font-style",
    "line-height", "letter-spacing", "text-align", "text-decoration", "text-transform",
    "color", "white-space", "word-wrap", "word-break",
    # Background
    "background", "background-color", "background-image", "background-position",
    "background-repeat", "background-size",
    # Other
    "opacity", "cursor", "box-shadow", "text-shadow", "transform", "transition",
])

# Protocols allowed in URLs
ALLOWED_PROTOCOLS = frozenset(["http", "https", "mailto", "tel"])


# =============================================================================
# SANITIZER CLASS
# =============================================================================

class HTMLSanitizer:
    """Sanitizes HTML content from AI output."""
    
    def __init__(self) -> None:
        self._css_sanitizer = CSSSanitizer(
            allowed_css_properties=list(ALLOWED_CSS_PROPERTIES)
        )
    
    def sanitize_html(self, html: str) -> str:
        """
        Sanitize HTML string.
        
        Args:
            html: Raw HTML string from AI
            
        Returns:
            Sanitized HTML string
        """
        if not html:
            return ""
        
        # Pre-process: remove dangerous patterns that might slip through
        html = self._remove_dangerous_patterns(html)
        
        # Main sanitization with bleach
        sanitized = bleach.clean(
            html,
            tags=ALLOWED_TAGS,
            attributes=ALLOWED_ATTRIBUTES,
            protocols=ALLOWED_PROTOCOLS,
            css_sanitizer=self._css_sanitizer,
            strip=True,
            strip_comments=True,
        )
        
        # Post-process: additional safety checks
        sanitized = self._post_process(sanitized)
        
        return sanitized
    
    def sanitize_url(self, url: str) -> Optional[str]:
        """
        Sanitize a URL value.
        
        Args:
            url: Raw URL string
            
        Returns:
            Sanitized URL or None if invalid
        """
        if not url:
            return None
        
        url = url.strip()
        
        # Block dangerous protocols
        dangerous_protocols = ["javascript:", "vbscript:", "data:", "file:"]
        url_lower = url.lower()
        for proto in dangerous_protocols:
            if url_lower.startswith(proto):
                logger.warning("Blocked dangerous URL protocol", url=url[:50])
                return None
        
        # Allow relative URLs
        if url.startswith("/") or url.startswith("#"):
            return url
        
        # Check protocol for absolute URLs
        if "://" in url:
            proto = url.split("://")[0].lower()
            if proto not in ALLOWED_PROTOCOLS:
                logger.warning("Blocked disallowed URL protocol", protocol=proto)
                return None
        
        return url
    
    def sanitize_text(self, text: str) -> str:
        """
        Sanitize plain text (escape HTML entities).
        
        Args:
            text: Raw text string
            
        Returns:
            Escaped text string
        """
        if not text:
            return ""
        return bleach.clean(text, tags=[], strip=True)
    
    def sanitize_class_name(self, class_name: str) -> str:
        """
        Sanitize CSS class names (Tailwind classes).
        
        Args:
            class_name: Raw class string
            
        Returns:
            Sanitized class string
        """
        if not class_name:
            return ""
        
        # Allow only safe characters in class names
        # Tailwind uses: alphanumeric, hyphens, underscores, colons, slashes, brackets
        safe_pattern = re.compile(r"^[a-zA-Z0-9\s\-_:\/\[\]\.\#]+$")
        
        if not safe_pattern.match(class_name):
            # Filter individual classes
            classes = class_name.split()
            safe_classes = [
                c for c in classes 
                if re.match(r"^[a-zA-Z0-9\-_:\/\[\]\.]+$", c)
            ]
            return " ".join(safe_classes)
        
        return class_name
    
    def _remove_dangerous_patterns(self, html: str) -> str:
        """Remove dangerous patterns before main sanitization."""
        # Remove script tags (even malformed)
        html = re.sub(r"<\s*script[^>]*>.*?<\s*/\s*script\s*>", "", html, flags=re.IGNORECASE | re.DOTALL)
        html = re.sub(r"<\s*script[^>]*>", "", html, flags=re.IGNORECASE)
        
        # Remove on* event handlers
        html = re.sub(r"\s+on\w+\s*=\s*['\"][^'\"]*['\"]", "", html, flags=re.IGNORECASE)
        html = re.sub(r"\s+on\w+\s*=\s*\S+", "", html, flags=re.IGNORECASE)
        
        # Remove javascript: URLs
        html = re.sub(r"javascript\s*:", "", html, flags=re.IGNORECASE)
        
        # Remove expression() in styles
        html = re.sub(r"expression\s*\([^)]*\)", "", html, flags=re.IGNORECASE)
        
        return html
    
    def _post_process(self, html: str) -> str:
        """Post-process sanitized HTML for additional safety."""
        # Ensure no javascript: URLs slipped through
        html = re.sub(r"javascript\s*:", "", html, flags=re.IGNORECASE)
        
        # Ensure no data: URLs in src attributes
        html = re.sub(r'src\s*=\s*["\']?\s*data:', 'src="', html, flags=re.IGNORECASE)
        
        return html


# =============================================================================
# TEMPLATE SANITIZER
# =============================================================================

class TemplateSanitizer:
    """Sanitizes complete template packages from AI."""
    
    def __init__(self) -> None:
        self._html_sanitizer = HTMLSanitizer()
    
    def sanitize_template(self, template: dict[str, Any]) -> dict[str, Any]:
        """
        Sanitize entire template package.
        
        Args:
            template: Raw template dict from AI
            
        Returns:
            Sanitized template dict
        """
        return self._sanitize_value(template)
    
    def _sanitize_value(self, value: Any, key: str = "") -> Any:
        """Recursively sanitize values based on their type and context."""
        if isinstance(value, str):
            return self._sanitize_string(value, key)
        elif isinstance(value, dict):
            return {k: self._sanitize_value(v, k) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._sanitize_value(item, key) for item in value]
        else:
            return value
    
    def _sanitize_string(self, value: str, key: str) -> str:
        """Sanitize a string value based on its context."""
        key_lower = key.lower()
        
        # URL fields
        if key_lower in ("href", "src", "action", "route"):
            return self._html_sanitizer.sanitize_url(value) or ""
        
        # Class names (Tailwind)
        if key_lower in ("class", "classname"):
            return self._html_sanitizer.sanitize_class_name(value)
        
        # HTML content fields
        if key_lower in ("content", "html", "body"):
            return self._html_sanitizer.sanitize_html(value)
        
        # Plain text fields
        return self._html_sanitizer.sanitize_text(value)


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================

_sanitizer: Optional[TemplateSanitizer] = None


def get_sanitizer() -> TemplateSanitizer:
    """Get template sanitizer instance."""
    global _sanitizer
    if _sanitizer is None:
        _sanitizer = TemplateSanitizer()
    return _sanitizer
