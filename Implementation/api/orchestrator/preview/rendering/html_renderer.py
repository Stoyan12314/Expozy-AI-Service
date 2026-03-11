"""
HTML renderer for EXPOZY v2.0 — Direct HTML mode.

The AI generates complete HTML for each page directly.
This renderer only handles:
  1. Wrapping content with the HTML page shell (head, CDN links, CSS)
  2. Concatenating header + content + footer HTML

No JSON→component dispatch. No hardcoded fallbacks. The AI is the renderer.
"""

import html as _html
from typing import List, Optional


# =============================================================================
# ESCAPE HELPERS (for page title / lang attr only)
# =============================================================================

def _esc(s: object) -> str:
    return _html.escape("" if s is None else str(s), quote=True)


def _esc_attr(s: object) -> str:
    return _html.escape("" if s is None else str(s), quote=True)


# =============================================================================
# MINIMAL CSS — only what Tailwind CDN can't handle
# =============================================================================

_CSS = """
/* EXPOZY CMS structure */
.is-section { position: relative; }
.is-section-auto { }
.is-overlay { position: absolute; inset: 0; pointer-events: none; z-index: 0; }
.is-overlay-bg { position: absolute; inset: 0; }
.is-container { position: relative; z-index: 1; }
.is-container.v2 { width: 100%; }
"""


# =============================================================================
# PAGE SHELL
# =============================================================================

def _build_html_page(body_html: str, title: str, lang: str) -> str:
    """Wrap body HTML with the full page shell (doctype, head, CDN links)."""
    return f"""<!DOCTYPE html>
<html lang="{_esc_attr(lang)}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{_esc(title)}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
    <style>{_CSS}</style>
</head>
<body class="bg-white dark:bg-gray-900 text-gray-900 dark:text-white antialiased">
{body_html}
</body>
</html>"""


# =============================================================================
# PUBLIC API
# =============================================================================

def render_page_with_layout(
    content: str,
    header: Optional[str] = None,
    footer: Optional[str] = None,
    title: str = "EXPOZY Preview",
    lang: str = "en",
) -> str:
    """
    Assemble a full HTML page from AI-generated HTML parts.

    Args:
        content: Raw HTML string for the page body (AI-generated).
        header:  Raw HTML string for the header/nav (AI-generated).
        footer:  Raw HTML string for the footer (AI-generated).
        title:   Page title for <title> tag.
        lang:    Language code for <html lang="...">.

    Returns:
        Complete HTML document string.
    """
    parts: List[str] = []

    if header:
        parts.append(header)
    if content:
        parts.append(content)
    if footer:
        parts.append(footer)

    body_html = "\n".join(parts)
    return _build_html_page(body_html, title, lang)