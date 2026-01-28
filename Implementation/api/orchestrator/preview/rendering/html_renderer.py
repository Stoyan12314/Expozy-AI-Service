"""
HTML renderer for preview bundles.

Pure function: takes a validated/sanitized template dict and returns HTML string.
No DB, no queue, no settings, no side effects.
"""

from __future__ import annotations

import html as _html
import re as _re
from urllib.parse import urlparse
from typing import Any, Dict


def render_template_to_html(template: Dict[str, Any]) -> str:
    """
    Render a template package (metadata/theme/sections) to a single HTML page.
    Supports section types:
    hero, features, products, testimonials, cta, form, footer (+ fallback).
    """
    metadata = template.get("metadata", {}) or {}
    theme = template.get("theme", {}) or {}
    sections = template.get("sections", []) or []

    primary_color = theme.get("primaryColor", "#3B82F6")
    dark_mode = bool(theme.get("darkMode", False))

    page_title = metadata.get("title") or metadata.get("name") or "Generated Page"
    page_desc = metadata.get("description") or ""

    def esc(s: object) -> str:
        return _html.escape("" if s is None else str(s), quote=True)

    def safe_class(s: object) -> str:
        s = "" if s is None else str(s)
        s = _re.sub(r"[^a-zA-Z0-9_\- ]+", "", s).strip()
        return s

    def safe_url(u: object) -> str:
        """Allow http(s) only. Return empty string if unsafe."""
        u = "" if u is None else str(u).strip()
        if not u:
            return ""
        try:
            p = urlparse(u)
            if p.scheme in ("http", "https"):
                return u.replace('"', "%22").replace("'", "%27")
        except Exception:
            pass
        return ""

    def render_buttons(buttons: list) -> str:
        out = []
        for btn in buttons or []:
            variant = (btn.get("variant") or "primary").lower()
            label = btn.get("text") or btn.get("label") or "Button"
            href = btn.get("href") or "#"

            btn_class = "btn-secondary" if variant in ("outline", "secondary") else "btn-primary"
            out.append(f'<a href="{esc(href)}" class="btn {btn_class}">{esc(label)}</a>')
        return "".join(out)

    def render_hero(section: dict) -> str:
        title = section.get("title", "")
        subtitle = section.get("subtitle", "")
        bg = safe_url(section.get("backgroundImage"))
        style = ""
        if bg:
            style = (
                ' style="'
                f"background-image:url('{bg}');"
                "background-size:cover;"
                "background-position:center;"
                '"'
            )

        return f"""
        <section class="section section-hero {safe_class(section.get('className'))}"{style}>
            <div class="hero-overlay"></div>
            <div class="hero-inner">
                {f'<h1 class="hero-title">{esc(title)}</h1>' if title else ''}
                {f'<p class="hero-subtitle">{esc(subtitle)}</p>' if subtitle else ''}
                <div class="section-buttons">{render_buttons(section.get("buttons", []))}</div>
            </div>
        </section>
        """

    def render_features(section: dict) -> str:
        title = section.get("title", "")
        subtitle = section.get("subtitle", "")
        cols = int(section.get("columns") or 3)
        cols = max(1, min(cols, 4))
        items = section.get("items", []) or []

        cards = []
        for item in items:
            it_title = item.get("title", "")
            it_content = item.get("content", "")
            it_icon = item.get("icon", "")
            cards.append(f"""
                <div class="card">
                    {f'<div class="card-icon">{esc(it_icon)}</div>' if it_icon else ''}
                    {f'<div class="card-title">{esc(it_title)}</div>' if it_title else ''}
                    {f'<div class="card-body">{esc(it_content)}</div>' if it_content else ''}
                </div>
            """)

        return f"""
        <section class="section section-features {safe_class(section.get('className'))}">
            {f'<h2 class="section-title">{esc(title)}</h2>' if title else ''}
            {f'<p class="section-subtitle">{esc(subtitle)}</p>' if subtitle else ''}
            <div class="grid" style="--cols:{cols};">
                {''.join(cards) if cards else '<div class="muted">No feature items provided.</div>'}
            </div>
        </section>
        """

    def render_products_like(section: dict, kind: str) -> str:
        title = section.get("title", "")
        subtitle = section.get("subtitle", "")
        cols = int(section.get("columns") or 3)
        cols = max(1, min(cols, 4))

        items = section.get("items")
        ds = section.get("dataSource")

        cards = []
        if isinstance(items, list) and items:
            for item in items:
                it_title = item.get("title") or item.get("name") or ""
                it_sub = item.get("subtitle") or item.get("role") or item.get("price") or ""
                it_content = item.get("content") or item.get("text") or item.get("description") or ""
                cards.append(f"""
                    <div class="card">
                        {f'<div class="card-title">{esc(it_title)}</div>' if it_title else ''}
                        {f'<div class="card-meta">{esc(it_sub)}</div>' if it_sub else ''}
                        {f'<div class="card-body">{esc(it_content)}</div>' if it_content else ''}
                    </div>
                """)
        else:
            label = f"Loaded from dataSource: {ds}" if ds else "No items/dataSource provided"
            for i in range(cols * 2):
                cards.append(f"""
                    <div class="card">
                        <div class="card-title">{esc(kind.title())} Item {i+1}</div>
                        <div class="card-body muted">{esc(label)}</div>
                    </div>
                """)

        return f"""
        <section class="section section-{kind} {safe_class(section.get('className'))}">
            {f'<h2 class="section-title">{esc(title)}</h2>' if title else ''}
            {f'<p class="section-subtitle">{esc(subtitle)}</p>' if subtitle else ''}
            <div class="grid" style="--cols:{cols};">
                {''.join(cards)}
            </div>
        </section>
        """

    def render_cta(section: dict) -> str:
        title = section.get("title", "")
        content = section.get("content", "")
        return f"""
        <section class="section section-cta {safe_class(section.get('className'))}">
            <div class="cta-inner">
                {f'<h2 class="section-title">{esc(title)}</h2>' if title else ''}
                {f'<div class="section-content">{esc(content)}</div>' if content else ''}
                <div class="section-buttons">{render_buttons(section.get("buttons", []))}</div>
            </div>
        </section>
        """

    def render_form(section: dict) -> str:
        title = section.get("title", "")
        subtitle = section.get("subtitle", "")
        fields = section.get("fields", []) or []

        form_fields = []
        for f in fields:
            name = f.get("name") or "field"
            label = f.get("label") or name
            ftype = f.get("type") or "text"
            placeholder = f.get("placeholder") or ""
            required = "required" if f.get("required") else ""
            form_fields.append(f"""
                <label class="form-field">
                    <span class="form-label">{esc(label)}</span>
                    <input class="input" name="{esc(name)}" type="{esc(ftype)}" placeholder="{esc(placeholder)}" {required}/>
                </label>
            """)

        return f"""
        <section class="section section-form {safe_class(section.get('className'))}">
            {f'<h2 class="section-title">{esc(title)}</h2>' if title else ''}
            {f'<p class="section-subtitle">{esc(subtitle)}</p>' if subtitle else ''}
            <form class="form" action="#" method="post">
                {''.join(form_fields)}
                <button type="submit" class="btn btn-primary">Submit</button>
            </form>
            <div class="muted form-note">Note: form submit is disabled in preview (no backend).</div>
        </section>
        """

    def render_footer(section: dict) -> str:
        content = section.get("content", "")
        items = section.get("items", []) or []
        links = []
        for it in items:
            t = it.get("title") or ""
            href = it.get("href") or "#"
            if t:
                links.append(f'<a class="footer-link" href="{esc(href)}">{esc(t)}</a>')
        return f"""
        <footer class="section section-footer {safe_class(section.get('className'))}">
            <div class="footer-inner">
                {f'<div class="footer-content">{esc(content)}</div>' if content else ''}
                <div class="footer-links">{''.join(links)}</div>
            </div>
        </footer>
        """

    def render_default(section: dict) -> str:
        sec_type = section.get("type", "content")
        title = section.get("title", "")
        subtitle = section.get("subtitle", "")
        content = section.get("content", "")
        return f"""
        <section class="section section-{esc(sec_type)} {safe_class(section.get('className'))}">
            {f'<h2 class="section-title">{esc(title)}</h2>' if title else ''}
            {f'<p class="section-subtitle">{esc(subtitle)}</p>' if subtitle else ''}
            {f'<div class="section-content">{esc(content)}</div>' if content else ''}
            <div class="section-buttons">{render_buttons(section.get("buttons", []))}</div>
        </section>
        """

    sections_html = []
    for section in sections:
        sec_type = (section.get("type") or "content").lower()
        if sec_type == "hero":
            sections_html.append(render_hero(section))
        elif sec_type == "features":
            sections_html.append(render_features(section))
        elif sec_type == "products":
            sections_html.append(render_products_like(section, "products"))
        elif sec_type == "testimonials":
            sections_html.append(render_products_like(section, "testimonials"))
        elif sec_type == "cta":
            sections_html.append(render_cta(section))
        elif sec_type == "form":
            sections_html.append(render_form(section))
        elif sec_type == "footer":
            sections_html.append(render_footer(section))
        else:
            sections_html.append(render_default(section))

    html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{esc(page_title)}</title>
    <meta name="description" content="{esc(page_desc)}">
    <style>
        :root {{
            --primary-color: {esc(primary_color)};
            --bg: {"#0b1220" if dark_mode else "#ffffff"};
            --fg: {"#e5e7eb" if dark_mode else "#111827"};
            --muted: {"#9ca3af" if dark_mode else "#6b7280"};
            --card: {"#0f172a" if dark_mode else "#f9fafb"};
            --border: {"rgba(255,255,255,0.08)" if dark_mode else "rgba(0,0,0,0.08)"};
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            line-height: 1.6;
            background: var(--bg);
            color: var(--fg);
        }}

        .section {{
            padding: 4rem 2rem;
            max-width: 1200px;
            margin: 0 auto;
        }}

        .section-title {{
            font-size: 2.2rem;
            font-weight: 800;
            margin-bottom: 0.75rem;
        }}

        .section-subtitle {{
            font-size: 1.1rem;
            color: var(--muted);
            margin-bottom: 1.75rem;
        }}

        .section-content {{
            font-size: 1.05rem;
            color: var(--fg);
            max-width: 900px;
        }}

        .muted {{ color: var(--muted); }}

        /* HERO */
        .section-hero {{
            position: relative;
            text-align: center;
            color: white;
            padding: 6rem 2rem;
            max-width: none;
            border-bottom: 1px solid var(--border);
            background: linear-gradient(135deg, var(--primary-color), #8b5cf6);
        }}
        .hero-overlay {{
            position:absolute; inset:0;
            background: rgba(0,0,0,0.45);
        }}
        .hero-inner {{
            position: relative;
            max-width: 900px;
            margin: 0 auto;
        }}
        .hero-title {{
            font-size: 3rem;
            font-weight: 900;
            line-height: 1.1;
            margin-bottom: 1rem;
        }}
        .hero-subtitle {{
            font-size: 1.2rem;
            opacity: 0.95;
        }}

        /* BUTTONS */
        .section-buttons {{
            display: flex;
            gap: 1rem;
            justify-content: center;
            flex-wrap: wrap;
            margin-top: 2rem;
        }}
        .btn {{
            display: inline-block;
            padding: 0.75rem 1.5rem;
            border-radius: 0.75rem;
            text-decoration: none;
            font-weight: 700;
            border: 1px solid transparent;
        }}
        .btn-primary {{
            background: white;
            color: #111827;
        }}
        .btn-secondary {{
            background: transparent;
            color: white;
            border-color: rgba(255,255,255,0.7);
        }}

        /* GRID/CARDS */
        .grid {{
            display: grid;
            grid-template-columns: repeat(var(--cols, 3), minmax(0, 1fr));
            gap: 1rem;
        }}
        .card {{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 1rem;
            padding: 1.25rem;
        }}
        .card-icon {{
            color: var(--muted);
            font-size: 0.9rem;
            margin-bottom: 0.5rem;
        }}
        .card-title {{
            font-weight: 800;
            margin-bottom: 0.35rem;
        }}
        .card-meta {{
            color: var(--muted);
            font-size: 0.9rem;
            margin-bottom: 0.75rem;
        }}
        .card-body {{
            color: var(--fg);
            font-size: 0.98rem;
        }}

        /* CTA */
        .section-cta {{
            max-width: none;
            background: #111827;
            color: white;
        }}
        .cta-inner {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        .section-cta .section-subtitle,
        .section-cta .section-content {{
            color: rgba(255,255,255,0.85);
        }}
        .section-cta .btn-primary {{
            background: var(--primary-color);
            color: white;
        }}

        /* FORM */
        .form {{
            display: grid;
            gap: 1rem;
            max-width: 520px;
        }}
        .form-field {{
            display: grid;
            gap: 0.4rem;
        }}
        .form-label {{
            color: var(--muted);
            font-size: 0.9rem;
            font-weight: 600;
        }}
        .input {{
            padding: 0.75rem 0.9rem;
            border-radius: 0.75rem;
            border: 1px solid var(--border);
            background: var(--card);
            color: var(--fg);
            outline: none;
        }}
        .form-note {{
            margin-top: 1rem;
            font-size: 0.9rem;
        }}

        /* FOOTER */
        .section-footer {{
            max-width: none;
            border-top: 1px solid var(--border);
            padding-top: 2rem;
            padding-bottom: 2rem;
        }}
        .footer-inner {{
            max-width: 1200px;
            margin: 0 auto;
            display: grid;
            gap: 1rem;
        }}
        .footer-links {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem 1rem;
        }}
        .footer-link {{
            color: var(--muted);
            text-decoration: none;
        }}
        .footer-link:hover {{
            color: var(--fg);
        }}

        @media (max-width: 900px) {{
            .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
        }}
        @media (max-width: 600px) {{
            .section {{ padding: 3rem 1rem; }}
            .grid {{ grid-template-columns: 1fr; }}
            .hero-title {{ font-size: 2.2rem; }}
        }}
    </style>
</head>
<body>
    {''.join(sections_html)}
</body>
</html>"""

    return html_out
