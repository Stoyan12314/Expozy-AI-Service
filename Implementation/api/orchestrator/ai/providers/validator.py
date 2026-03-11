import json
import os
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Set, Tuple


# ============================================================================
# CATALOG LOADER — reads pre-computed _validator section from combined_catalog
# ============================================================================

def _load_validator_section() -> Dict[str, Any]:
    catalog = None

    try:
        from api.orchestrator.ai.providers.catalog_loader import get_catalog
        cat = get_catalog()
        raw = getattr(cat, "_catalog", None) or getattr(cat, "catalog", None)
        if isinstance(raw, dict) and "_validator" in raw:
            catalog = raw
    except Exception:
        pass

    if not catalog:
        candidates = [
            os.environ.get("EXPOZY_CATALOG_PATH", ""),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "combined_catalog.json"),
            "/app/api/orchestrator/ai/providers/schemas/combined_catalog.json",
            "/app/combined_catalog.json",
            "combined_catalog.json",
        ]
        for path in candidates:
            if path and os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and "_validator" in data:
                        catalog = data
                        break
                except Exception:
                    continue

    if not catalog or "_validator" not in catalog:
        return {}

    return catalog["_validator"]


def _to_set(val: Any) -> Set[str]:
    if isinstance(val, (list, tuple)):
        return set(val)
    if isinstance(val, set):
        return val
    return set()


def _to_list(val: Any) -> List[str]:
    if isinstance(val, (list, tuple)):
        return list(val)
    return []


def _to_dict(val: Any) -> Dict[str, str]:
    if isinstance(val, dict):
        return val
    return {}


def _to_field_map(val: Any) -> Dict[str, Optional[Set[str]]]:
    result: Dict[str, Optional[Set[str]]] = {}
    if not isinstance(val, dict):
        return result
    for key, fields in val.items():
        if fields is None:
            result[key] = None
        elif isinstance(fields, (list, tuple)):
            result[key] = set(fields)
        elif isinstance(fields, set):
            result[key] = fields
        else:
            result[key] = None
    return result


# ============================================================================
# HTML PARSER — full tag tracking with open/close matching
# ============================================================================

@dataclass
class ParsedTag:
    """Single HTML element with attributes and position info."""
    name: str
    attrs: Dict[str, str]
    line: int = 0
    self_closing: bool = False

    def get(self, key: str, default: str = "") -> str:
        return self.attrs.get(key, default)

    def has(self, key: str) -> bool:
        return key in self.attrs

    def loc(self) -> str:
        tid = self.attrs.get("id", "")
        cls = self.attrs.get("class", "")
        parts = [f"<{self.name}"]
        if tid:
            parts.append(f' id="{tid}"')
        if cls:
            parts.append(f' class="{cls[:50]}"')
        parts.append(">")
        if self.line:
            parts.append(f" (line {self.line})")
        return "".join(parts)

    def alpine_attrs(self) -> List[Tuple[str, str]]:
        return [
            (k, v) for k, v in self.attrs.items()
            if k.startswith(("x-", "@", ":"))
        ]


VOID_ELEMENTS: Set[str] = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

DEPRECATED_ELEMENTS: Set[str] = {
    "acronym", "applet", "basefont", "bgsound", "big", "blink",
    "center", "dir", "font", "frame", "frameset", "hgroup",
    "isindex", "keygen", "listing", "marquee", "menuitem",
    "multicol", "nextid", "nobr", "noembed", "noframes",
    "plaintext", "rb", "rtc", "spacer", "strike", "tt", "xmp",
}

INLINE_ELEMENTS: Set[str] = {
    "a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data",
    "dfn", "em", "i", "kbd", "mark", "q", "rp", "rt", "ruby",
    "s", "samp", "small", "span", "strong", "sub", "sup", "time",
    "u", "var", "wbr",
}

BLOCK_ELEMENTS: Set[str] = {
    "address", "article", "aside", "blockquote", "details", "dialog",
    "dd", "div", "dl", "dt", "fieldset", "figcaption", "figure",
    "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hgroup", "hr", "li", "main", "nav", "ol", "p",
    "pre", "section", "table", "ul",
}


class _FullParser(HTMLParser):
    """
    Extended parser that tracks:
    - All start tags with attributes → self.tags
    - Open/close tag matching → self.unclosed, self.mismatched
    - Nesting violations → self.nesting_errors
    - Duplicate IDs → self.duplicate_ids
    - Duplicate attributes on same element → self.duplicate_attrs
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags: List[ParsedTag] = []
        self.unclosed: List[Tuple[str, int]] = []
        self.mismatched: List[Tuple[str, int]] = []
        self.nesting_errors: List[str] = []
        self.duplicate_ids: List[Tuple[str, int]] = []
        self.duplicate_attrs: List[Tuple[str, str, int]] = []  # (tag, attr, line)
        self._deprecated: List[Tuple[str, int]] = []

        self._stack: List[Tuple[str, int]] = []
        self._seen_ids: Dict[str, int] = {}

    def handle_starttag(self, tag: str, attrs: list):
        tag_lower = tag.lower()
        line = self.getpos()[0]

        # ── Detect duplicate attributes (before dedup into dict) ─────
        seen_attr_names: Dict[str, int] = {}
        for name, value in attrs:
            key = (name or "").lower()
            if key in seen_attr_names:
                self.duplicate_attrs.append((tag_lower, key, line))
            else:
                seen_attr_names[key] = 1

        attr_dict: Dict[str, str] = {}
        for name, value in attrs:
            key = name.lower() if name else ""
            val = value if value is not None else ""
            attr_dict[key] = val

        self.tags.append(ParsedTag(
            name=tag_lower, attrs=attr_dict, line=line,
        ))

        eid = attr_dict.get("id", "")
        if eid:
            if eid in self._seen_ids:
                self.duplicate_ids.append((eid, line))
            else:
                self._seen_ids[eid] = line

        if tag_lower in DEPRECATED_ELEMENTS:
            self._deprecated.append((tag_lower, line))

        if tag_lower in BLOCK_ELEMENTS and self._stack:
            parent_tag = self._stack[-1][0]
            parent_line = self._stack[-1][1]
            if parent_tag in INLINE_ELEMENTS:
                self.nesting_errors.append(
                    f"Block <{tag_lower}> (line {line}) inside inline "
                    f"<{parent_tag}> (line {parent_line})"
                )
            elif parent_tag == "p":
                self.nesting_errors.append(
                    f"Block <{tag_lower}> (line {line}) inside <p> "
                    f"(line {parent_line}) — <p> auto-closes"
                )

        if tag_lower not in VOID_ELEMENTS:
            self._stack.append((tag_lower, line))

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        line = self.getpos()[0]

        if tag_lower in VOID_ELEMENTS:
            return

        if not self._stack:
            self.mismatched.append((tag_lower, line))
            return

        if self._stack[-1][0] == tag_lower:
            self._stack.pop()
            return

        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag_lower:
                for j in range(len(self._stack) - 1, i, -1):
                    unc = self._stack.pop()
                    self.unclosed.append(unc)
                self._stack.pop()
                return

        self.mismatched.append((tag_lower, line))

    def handle_startendtag(self, tag: str, attrs: list):
        self.handle_starttag(tag, attrs)
        tag_lower = tag.lower()
        if tag_lower not in VOID_ELEMENTS and self._stack:
            if self._stack[-1][0] == tag_lower:
                self._stack.pop()

    def close(self):
        super().close()
        while self._stack:
            self.unclosed.append(self._stack.pop())


def parse_html(html: str) -> Tuple[_FullParser, List[ParsedTag]]:
    parser = _FullParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    return parser, parser.tags


# ============================================================================
# LAYER 0: HTML SYNTAX (W3C Nu-validator style)
# ============================================================================

def check_html_syntax(parser: _FullParser, tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []

    skip_unclosed = {"template", "html", "head", "body"}
    for tag_name, line in parser.unclosed:
        if tag_name not in skip_unclosed:
            errors.append(f"HTM-001 Unclosed <{tag_name}> (opened line {line})")

    for tag_name, line in parser.mismatched:
        errors.append(f"HTM-002 Unexpected </{tag_name}> (line {line}) — no matching open tag")

    for msg in parser.nesting_errors:
        errors.append(f"HTM-003 Nesting violation: {msg}")

    for eid, line in parser.duplicate_ids:
        first = parser._seen_ids.get(eid, "?")
        errors.append(f"HTM-004 Duplicate id=\"{eid}\" (line {line}, first at line {first})")

    for tag_name, line in parser._deprecated:
        errors.append(f"HTM-005 Deprecated element <{tag_name}> (line {line})")

    for tag in tags:
        reqs = REQUIRED_ATTRS.get(tag.name)
        if not reqs:
            continue
        for attr in reqs:
            bound = f":{attr}"
            if not tag.has(attr) and not tag.has(bound):
                errors.append(
                    f"HTM-006 <{tag.name}> missing required '{attr}' "
                    f"attribute {tag.loc()}"
                )

    for tag in tags:
        if tag.name == "img":
            if not tag.has("src") and not tag.has(":src"):
                errors.append(f"HTM-006 <img> missing src {tag.loc()}")

    return errors


# ============================================================================
# LAYER 1: SECURITY (OWASP XSS Prevention + CSP + Bleach-style allowlist)
# ============================================================================

SAFE_URL_SCHEMES = {"http", "https", "mailto", "tel", "#", "/"}
UNSAFE_URL_SCHEME_RE = re.compile(r"^\s*(javascript|data|vbscript|blob)\s*:", re.I)
INLINE_HANDLER_RE = re.compile(r"^on[a-z]+$", re.I)

CSS_INJECTION_PATTERNS = [
    (re.compile(r"expression\s*\(", re.I),                 "CSS expression()"),
    (re.compile(r"@import\s+", re.I),                      "CSS @import"),
    (re.compile(r"url\s*\(\s*['\"]?\s*data:", re.I),       "data: in CSS url()"),
    (re.compile(r"url\s*\(\s*['\"]?\s*javascript:", re.I), "javascript: in CSS url()"),
    (re.compile(r"-moz-binding\s*:", re.I),                "-moz-binding (XSS vector)"),
    (re.compile(r"behavior\s*:", re.I),                    "behavior: (IE XSS vector)"),
]

DANGEROUS_ATTR_RE = re.compile(
    r"javascript\s*:|data\s*:text/html|vbscript\s*:|livescript\s*:", re.I
)


def check_forbidden_elements(tags: List[ParsedTag]) -> List[str]:
    return [
        f"SEC-001 Forbidden element <{t.name}> {t.loc()}"
        for t in tags if t.name in FORBIDDEN_ELEMENTS
    ]


def check_restricted_elements(tags: List[ParsedTag]) -> List[str]:
    return [
        f"SEC-002 Restricted element <{t.name}> {t.loc()} — {RESTRICTED_ELEMENTS[t.name]}"
        for t in tags if t.name in RESTRICTED_ELEMENTS
    ]


def check_element_allowlist(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    for t in tags:
        if t.name not in ALLOWED_ELEMENTS and t.name not in FORBIDDEN_ELEMENTS:
            errors.append(
                f"SEC-003 Disallowed element <{t.name}> {t.loc()} — "
                f"not in allowlist"
            )
    return errors


def check_iframe_sources(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    for t in tags:
        if t.name != "iframe":
            continue
        src = t.get("src")
        if src and not any(d in src for d in ALLOWED_IFRAME_DOMAINS):
            errors.append(f"SEC-004 Iframe disallowed source: {src[:100]}")
        if t.has("srcdoc"):
            errors.append(
                f"SEC-004 Iframe srcdoc= on {t.loc()} — "
                f"allows inline HTML injection"
            )
    return errors


def check_unsafe_urls(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    url_attrs = ("href", "src", "action", "formaction", "poster", "data")
    for t in tags:
        for attr_name in url_attrs:
            val = t.get(attr_name)
            if val and UNSAFE_URL_SCHEME_RE.match(val):
                errors.append(
                    f"SEC-005 Unsafe URL in {attr_name}=\"{val[:60]}\" on {t.loc()}"
                )
        for bound in (":href", ":src", ":action"):
            val = t.get(bound)
            if val and DANGEROUS_ATTR_RE.search(val):
                errors.append(
                    f"SEC-005 Unsafe scheme in {bound}=\"{val[:60]}\" on {t.loc()}"
                )
    return errors


def check_inline_handlers(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    for t in tags:
        for attr_name in t.attrs:
            if INLINE_HANDLER_RE.match(attr_name) and not attr_name.startswith(("x-", "@", ":")):
                errors.append(f"SEC-006 Inline handler '{attr_name}' on {t.loc()}")
    return errors


def check_css_injection(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    for t in tags:
        style = t.get("style")
        if style:
            for pat, name in CSS_INJECTION_PATTERNS:
                if pat.search(style):
                    errors.append(f"SEC-007 {name} in style on {t.loc()}")
    return errors


def check_meta_in_body(tags: List[ParsedTag]) -> List[str]:
    return [
        "SEC-008 <meta http-equiv> can override security headers"
        for t in tags if t.name == "meta" and t.has("http-equiv")
    ]


def check_target_blank(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    for t in tags:
        if t.name == "a" and t.get("target") == "_blank":
            rel = t.get("rel", "").lower()
            if "noopener" not in rel:
                errors.append(
                    f"SEC-009 <a target=\"_blank\"> missing rel=\"noopener\" "
                    f"{t.loc()} — tabnabbing risk"
                )
    return errors


def check_form_action_safety(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    external_re = re.compile(r"^https?://", re.I)
    for t in tags:
        if t.name == "form":
            action = t.get("action")
            if action and UNSAFE_URL_SCHEME_RE.match(action):
                errors.append(f"SEC-010 Form action unsafe URL: {action[:60]} on {t.loc()}")
            if action and external_re.match(action):
                errors.append(
                    f"SEC-010 Form action external URL: {action[:60]} on {t.loc()} "
                    f"— EXPOZY forms submit via alpineListeners()"
                )
    return errors


def check_dangerous_attrs(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    skip_prefixes = ("x-", "@", ":", "class", "style", "id")
    for t in tags:
        for attr_name, attr_val in t.attrs.items():
            if any(attr_name.startswith(p) for p in skip_prefixes):
                continue
            if attr_val and DANGEROUS_ATTR_RE.search(attr_val):
                errors.append(
                    f"SEC-011 Dangerous value in {attr_name}=\"{attr_val[:60]}\" "
                    f"on {t.loc()}"
                )
    return errors


# ============================================================================
# CATALOG-DRIVEN ALLOWLISTS
# ============================================================================

_V = _load_validator_section()
_CATALOG_AVAILABLE: bool = bool(_V)

ALLOWED_DIRECTIVE_BASES: Set[str] = _to_set(_V.get("allowed_alpine_directives")) or {
    "x-data", "x-init", "x-if", "x-for", "x-text", "x-html",
    "x-show", "x-bind", "x-on", "x-cloak", "x-model",
    "x-transition", "x-amount",
}

_FORM_SUBMIT_EVENTS: Set[str] = {"@submit"}

ALLOWED_EVENT_BASES: Set[str] = (_to_set(_V.get("allowed_alpine_events")) or {
    "@click", "@input", "@change", "@success",
    "@mouseenter", "@mouseleave",
}) | _FORM_SUBMIT_EVENTS

ALLOWED_BIND_TARGETS: Set[str] = _to_set(_V.get("allowed_alpine_bindings")) or {
    ":class", ":src", ":href", ":value", ":selected", ":alt",
    ":data-id", ":data-subject_id", ":data-tag", ":data-page",
    ":data-category_id", ":key",
}

FORBIDDEN_ELEMENTS: Set[str] = _to_set(_V.get("forbidden_html_elements")) or {
    "script", "object", "embed", "base",
}

ALLOWED_ELEMENTS: Set[str] = _to_set(_V.get("allowed_html_elements")) or {
    "html", "head", "body", "main", "div", "span", "section", "article",
    "aside", "header", "footer", "nav", "template",
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "hr", "pre",
    "blockquote", "code", "em", "strong", "b", "i", "u", "s",
    "small", "sub", "sup", "mark", "del", "ins", "abbr", "time",
    "cite", "dfn", "kbd", "samp", "var", "wbr",
    "ul", "ol", "li", "dl", "dt", "dd",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
    "colgroup", "col",
    "form", "input", "textarea", "select", "option", "optgroup",
    "button", "label", "fieldset", "legend", "datalist", "output",
    "img", "picture", "source", "figure", "figcaption", "video",
    "audio", "iframe",
    "a",
    "meta", "link", "title",
    "details", "summary", "dialog", "slot", "noscript",
    "svg", "path", "circle", "rect", "line", "polyline", "polygon",
    "g", "defs", "clippath", "use", "text", "tspan",
    "lineargradient", "radialgradient", "stop", "mask",
}

ALLOWED_IFRAME_DOMAINS: Set[str] = _to_set(_V.get("allowed_iframe_domains")) or {
    "maps.google.com",
}

JS_BUILTIN_METHODS: Set[str] = _to_set(_V.get("js_builtin_methods")) or {
    "find", "filter", "map", "reduce", "forEach", "some", "every",
    "includes", "indexOf", "slice", "join", "split", "trim",
    "replace", "match", "push", "pop", "shift", "concat",
    "sort", "reverse", "flat", "flatMap", "fill", "keys", "values",
    "entries", "at", "findIndex",
    "toString", "toLowerCase", "toUpperCase", "startsWith", "endsWith",
    "substring", "charAt", "padStart", "padEnd", "repeat",
    "charCodeAt", "normalize", "localeCompare",
    "hasOwnProperty", "valueOf", "toFixed", "toPrecision",
    "toLocaleDateString", "toLocaleTimeString", "toLocaleString",
    "toISOString", "toDateString", "toTimeString", "toUTCString",
    "getFullYear", "getMonth", "getDate", "getDay",
    "getHours", "getMinutes", "getSeconds", "getTime",
    "setFullYear", "setMonth", "setDate",
}

REQUIRED_ATTRS: Dict[str, List[str]] = _V.get("required_element_attrs", {
    "img":  ["alt"],
    "html": ["lang"],
})

# ============================================================================
# CATALOG: VALIDATOR_HINTS
# ============================================================================

RESTRICTED_ELEMENTS: Dict[str, str] = _to_dict(_V.get("restricted_html_elements")) or {
    "math":  "MathML can contain script — verify content",
    "style": "Inline <style> can contain CSS injection vectors",
}

CDN_DOMAINS: List[str] = _to_list(_V.get("cdn_domains")) or [
    "cdn.tailwindcss.com", "unpkg.com", "cdn.jsdelivr.net", "cdnjs.cloudflare.com",
]

SHADOW_DATA_VARS: Set[str] = _to_set(_V.get("shadow_data_vars")) or {
    "blogPosts", "blogCategories", "blogPosts_filters", "menu",
    "blogReviews", "post", "comments", "sameCategoryPosts", "newestPosts",
}

SKIP_FUNCTIONS: Set[str] = (_to_set(_V.get("skip_functions")) or {
    "alpineListeners", "alert", "confirm", "prompt",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "requestAnimationFrame",
    "parseInt", "parseFloat", "isNaN", "isFinite",
    "encodeURIComponent", "decodeURIComponent",
    "String", "Number", "Boolean", "Array", "Object", "Date",
    "Math", "JSON", "console", "Error",
    "event", "window", "document",
}) | JS_BUILTIN_METHODS

ALLOWED_DOTTED_PREFIXES: Set[str] = _to_set(_V.get("allowed_dotted_prefixes")) or {
    "Math", "JSON", "Object", "Array", "String", "Number",
    "Date", "console", "Helpers", "DateHelper", "Gallery",
}

_DIRECT_CALL_MODULES: List[str] = _to_list(_V.get("direct_call_modules")) or [
    "Reviews", "Newsletter", "Contacts", "User", "Shop",
    "Gallery", "Countries", "DateHelper",
]
_DIRECT_CALL_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(m) for m in _DIRECT_CALL_MODULES) + r")\.\w+\s*\("
)

_raw_vanilla = _V.get("vanilla_js_patterns")
if _raw_vanilla and isinstance(_raw_vanilla, list):
    VANILLA_JS_PATTERNS = [
        (re.compile(p["pattern"]), p["description"])
        for p in _raw_vanilla
        if isinstance(p, dict) and "pattern" in p and "description" in p
    ]
else:
    VANILLA_JS_PATTERNS = [
        (re.compile(r"\bdocument\.\w+"),        "document.* DOM access"),
        (re.compile(r"\.classList\b"),           ".classList manipulation"),
        (re.compile(r"\.style\.\w+\s*="),       ".style.* direct manipulation"),
        (re.compile(r"\.innerHTML\b"),           ".innerHTML direct access"),
        (re.compile(r"\.textContent\b"),         ".textContent direct access"),
        (re.compile(r"\.querySelector\b"),       ".querySelector direct access"),
        (re.compile(r"\.getElementById\b"),      ".getElementById direct access"),
        (re.compile(r"\.addEventListener\b"),    ".addEventListener direct access"),
        (re.compile(r"\.removeEventListener\b"), ".removeEventListener"),
        (re.compile(r"\.appendChild\b"),         ".appendChild DOM manipulation"),
        (re.compile(r"\.removeChild\b"),         ".removeChild DOM manipulation"),
        (re.compile(r"\.setAttribute\b"),        ".setAttribute direct access"),
    ]

SECTION_WRAPPERS: Dict[str, str] = _to_dict(_V.get("section_wrappers")) or {
    "section_root_class": "is-section",
    "overlay_bg_class": "is-overlay-bg",
    "overlay_class": "is-overlay",
    "correct_container": "!container is-container v2",
    "wrong_container_markers": "container,mx-auto",
}

ERROR_HINTS: Dict[str, str] = _to_dict(_V.get("error_hints")) or {}

# ============================================================================
# CATALOG: ALLOWED LISTENERS, ENDPOINTS, KEYNAMES, PATHS, FUNCTIONS, STATE
# ============================================================================

ALLOWED_LISTENERS: Set[str]                 = _to_set(_V.get("allowed_listeners"))
ALLOWED_API_ENDPOINTS: Set[str]             = _to_set(_V.get("allowed_api_endpoints"))
IMPLICIT_KEYNAME_ENDPOINTS: Set[str]        = _to_set(_V.get("implicit_keyname_endpoints"))
ENDPOINT_KEYNAME_MAP: Dict[str, str]        = _V.get("endpoint_keyname_map", {})
ALLOWED_LOOP_SOURCES: Set[str]              = _to_set(_V.get("allowed_loop_sources"))
LOOP_ITEM_FIELDS: Dict[str, Optional[Set[str]]] = _to_field_map(_V.get("loop_item_fields", {}))

_JS_BUILTINS: Set[str] = {
    "Math.floor", "Math.ceil", "Math.round", "Math.min", "Math.max",
    "parseInt", "parseFloat", "JSON.stringify", "JSON.parse",
    "Date.now", "String.fromCharCode",
    "console.log", "console.error",
    "Object.keys", "Object.values", "Object.entries",
    "Array.isArray", "Array.from",
    "encodeURIComponent", "decodeURIComponent",
}
ALLOWED_FUNCTIONS: Set[str] = _to_set(_V.get("allowed_functions")) | _JS_BUILTINS

_ALPINE_LOCAL_STATE: Set[str] = {
    "data",
    "tab", "activeTab", "activeCategory", "open", "isOpen",
    "showForm", "loading", "error", "success", "selected",
    "step", "currentSlide", "agreed",
}
ALLOWED_STATE_VARS: Set[str] = _to_set(_V.get("allowed_state_vars")) | _ALPINE_LOCAL_STATE

WRONG_LOOP_SOURCES: Dict[str, str]              = _V.get("wrong_loop_sources", {})
WRONG_FUNCTIONS: Dict[str, str]                  = _V.get("wrong_functions", {})
WRONG_STATE_VARS: Dict[str, str]                 = _V.get("wrong_state_vars", {})
WRONG_FIELDS_BY_CONTEXT: Dict[str, Dict[str, str]] = _V.get("wrong_fields_by_context", {})
KNOWN_WRONG_FIELDS_GENERAL: Dict[str, str]       = _V.get("known_wrong_fields_general", {})

if _CATALOG_AVAILABLE:
    print(
        f"[validator] Loaded from _validator: "
        f"{len(ALLOWED_API_ENDPOINTS)} endpoints, "
        f"{len(ALLOWED_LISTENERS)} listeners, "
        f"{len(ENDPOINT_KEYNAME_MAP)} keyName maps, "
        f"{len(ALLOWED_LOOP_SOURCES)} loop sources, "
        f"{len(LOOP_ITEM_FIELDS)} field schemas, "
        f"{len(ALLOWED_STATE_VARS)} state vars, "
        f"{len(ALLOWED_DIRECTIVE_BASES)} directives, "
        f"{len(ALLOWED_EVENT_BASES)} events, "
        f"{len(ALLOWED_BIND_TARGETS)} bindings, "
        f"{len(ALLOWED_ELEMENTS)} html elements, "
        f"{len(FORBIDDEN_ELEMENTS)} forbidden elements, "
        f"{len(ALLOWED_IFRAME_DOMAINS)} iframe domains, "
        f"{len(JS_BUILTIN_METHODS)} JS builtins, "
        f"{len(RESTRICTED_ELEMENTS)} restricted elements, "
        f"{len(CDN_DOMAINS)} CDN domains, "
        f"{len(SHADOW_DATA_VARS)} shadow data vars, "
        f"{len(SKIP_FUNCTIONS)} skip functions, "
        f"{len(ALLOWED_DOTTED_PREFIXES)} dotted prefixes, "
        f"{len(_DIRECT_CALL_MODULES)} direct-call modules, "
        f"{len(VANILLA_JS_PATTERNS)} vanilla JS patterns, "
        f"{len(SECTION_WRAPPERS)} section wrapper keys, "
        f"{len(ERROR_HINTS)} error hints"
    )
else:
    print("[validator] No _validator section found in catalog — binding checks limited")

# ============================================================================
# REGEX HELPERS
# ============================================================================

_LISTENER_RE = re.compile(r"alpineListeners\s*\(\s*['\"]([^'\"]+)['\"]")
_DISPATCH_RE = re.compile(r"\$dispatch\s*\(")
_FETCH_RE = re.compile(r"\bfetch\s*\(")
_XHR_RE = re.compile(r"\bXMLHttpRequest\b")
_API_PATH_RE = re.compile(r"/api/")
_AXIOS_RE = re.compile(r"\baxios\b")
_FOR_EXPR_RE = re.compile(
    r"\(?\s*(\w+)\s*(?:,\s*\w+)?\s*\)?\s+in\s+([\w.]+(?:\.\w+)*)"
)
_FIELD_ACCESS_RE = re.compile(r"\b(\w+)\.([\w.\[\]]+)")
_FUNCTION_CALL_RE = re.compile(r"\b([a-zA-Z_][\w.]*)\s*\(")
_XDATA_VAR_RE = re.compile(r"(\w+)\s*:")
_STATE_REF_RE = re.compile(r"\b([a-zA-Z_]\w+)\b")


def _normalize_directive(attr: str) -> str:
    base = attr.split(".")[0]
    if base.startswith("x-transition"):
        return "x-transition"
    return base


def _is_safe_data_binding(attr_name: str) -> bool:
    if attr_name.startswith(":data-"):
        return True
    if attr_name.startswith("x-bind:data-"):
        return True
    return False


# ============================================================================
# LAYER 2: ALPINE.JS COMPLIANCE
# ============================================================================

def check_alpine_directives(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    for t in tags:
        for attr_name, _ in t.alpine_attrs():
            if attr_name.startswith("x-bind:"):
                sh = ":" + attr_name[7:]
                if _is_safe_data_binding(attr_name):
                    continue
                if sh == ":key" and t.name == "template" and t.has("x-for"):
                    continue
                if sh not in ALLOWED_BIND_TARGETS:
                    errors.append(f"ALP-001 Disallowed binding {attr_name} on {t.loc()}")
                continue
            if attr_name.startswith("x-on:"):
                sh = "@" + attr_name[5:]
                if _normalize_directive(sh) not in ALLOWED_EVENT_BASES:
                    errors.append(f"ALP-001 Disallowed event {attr_name} on {t.loc()}")
                continue
            base = _normalize_directive(attr_name)
            if attr_name.startswith("@"):
                if base not in ALLOWED_EVENT_BASES:
                    errors.append(f"ALP-001 Disallowed event '{attr_name}' on {t.loc()}")
            elif attr_name.startswith(":"):
                if _is_safe_data_binding(attr_name):
                    continue
                if base == ":key" and t.name == "template" and t.has("x-for"):
                    continue
                if base not in ALLOWED_BIND_TARGETS:
                    errors.append(f"ALP-001 Disallowed binding '{attr_name}' on {t.loc()}")
            elif attr_name.startswith("x-"):
                if base not in ALLOWED_DIRECTIVE_BASES:
                    errors.append(f"ALP-001 Disallowed directive '{attr_name}' on {t.loc()}")
    return errors


def check_alpine_listeners(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    if not ALLOWED_LISTENERS:
        return errors
    for t in tags:
        for attr_name, attr_val in t.alpine_attrs():
            for m in _LISTENER_RE.finditer(attr_val):
                if m.group(1) not in ALLOWED_LISTENERS:
                    errors.append(
                        f"ALP-002 Disallowed listener '{m.group(1)}' "
                        f"in {attr_name} on {t.loc()}"
                    )
    return errors


def check_xhtml_usage(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    for t in tags:
        v = t.get("x-html")
        if v and "data.post.description" not in v:
            errors.append(
                f"ALP-003 x-html for non-CMS content: \"{v[:80]}\" on {t.loc()}"
            )
    return errors


def check_navigation_patterns(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    for t in tags:
        for attr_name, attr_val in t.alpine_attrs():
            if _DISPATCH_RE.search(attr_val):
                errors.append(f"ALP-004 $dispatch() in {attr_name} on {t.loc()} — use href(url)")
            if _DIRECT_CALL_RE.search(attr_val) and "alpineListeners" not in attr_val:
                errors.append(
                    f"ALP-005 Direct module call in {attr_name} on {t.loc()} "
                    f"— use alpineListeners(): \"{attr_val[:80]}\""
                )
    return errors


def check_form_patterns(tags: List[ParsedTag]) -> List[str]:
    return [
        f"ALP-006 @submit on {t.loc()} — use @submit.prevent or button @click with alpineListeners()"
        for t in tags for a in t.attrs if a == "@submit"
    ]


def check_xfor_on_template(tags: List[ParsedTag]) -> List[str]:
    return [
        f"ALP-007 x-for on <{t.name}> — must be on <template>"
        for t in tags if t.get("x-for") and t.name != "template"
    ]


# ============================================================================
# LAYER 3: EXPOZY DATA BINDING
# ============================================================================

def check_api_bindings(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    for t in tags:
        api = t.get("apidata")
        if api:
            if ALLOWED_API_ENDPOINTS and api not in ALLOWED_API_ENDPOINTS:
                errors.append(f"BND-001 Unknown apiData '{api}' on {t.loc()}")
            if not t.get("keyname") and api not in IMPLICIT_KEYNAME_ENDPOINTS:
                errors.append(f"BND-002 apiData='{api}' missing keyName on {t.loc()}")
    return errors


def check_no_custom_fetch(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    for t in tags:
        for a in ("x-data", "x-init"):
            v = t.get(a)
            if not v:
                continue
            if _FETCH_RE.search(v):
                errors.append(f"BND-003 fetch() in {a} on {t.loc()} — use apiData/keyName")
            if _XHR_RE.search(v):
                errors.append(f"BND-003 XMLHttpRequest in {a} on {t.loc()} — use apiData/keyName")
            if _API_PATH_RE.search(v):
                errors.append(f"BND-004 REST /api/ in {a} on {t.loc()} — use dot-notation")
            if _AXIOS_RE.search(v):
                errors.append(f"BND-003 axios in {a} on {t.loc()} — use apiData/keyName")
    return errors


def check_for_loop_sources(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    for t in tags:
        xf = t.get("x-for")
        if not xf:
            continue
        m = _FOR_EXPR_RE.search(xf)
        if m:
            src = m.group(2)
            if not src.startswith("data.") and not src.isdigit() and src in SHADOW_DATA_VARS:
                errors.append(
                    f"BND-005 x-for source '{src}' on {t.loc()} — should be data.{src}"
                )
    return errors


# ============================================================================
# LAYER 3b: EXPOZY BINDING ACCURACY
# ============================================================================

def check_keyname_values(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    for t in tags:
        api = t.get("apidata")
        keyname = t.get("keyname")
        if not api or not keyname:
            continue
        expected = ENDPOINT_KEYNAME_MAP.get(api)
        if expected and keyname != expected:
            errors.append(
                f"BND-010 Wrong keyName='{keyname}' for apiData='{api}' "
                f"on {t.loc()} — must be keyName='{expected}'"
            )
    return errors


def check_loop_source_paths(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    for t in tags:
        xf = t.get("x-for")
        if not xf:
            continue
        m = _FOR_EXPR_RE.search(xf)
        if not m:
            continue
        src = m.group(2)
        if not src.startswith("data."):
            continue
        if src in WRONG_LOOP_SOURCES:
            correct = WRONG_LOOP_SOURCES[src]
            errors.append(
                f"BND-011 Wrong x-for source '{src}' on {t.loc()} "
                f"— use '{correct}'"
            )
        elif ALLOWED_LOOP_SOURCES and src not in ALLOWED_LOOP_SOURCES:
            errors.append(
                f"BND-012 Unknown x-for source '{src}' on {t.loc()} "
                f"— not a known EXPOZY data path. "
                f"Valid sources: {sorted(ALLOWED_LOOP_SOURCES)}"
            )
    return errors


def check_unknown_functions(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []

    for t in tags:
        for attr_name, attr_val in t.alpine_attrs():
            if not attr_val:
                continue

            for fm in _FUNCTION_CALL_RE.finditer(attr_val):
                func_name = fm.group(1)

                if func_name in SKIP_FUNCTIONS:
                    continue
                if func_name in ALLOWED_FUNCTIONS:
                    continue
                if "." in func_name:
                    if func_name in ALLOWED_FUNCTIONS:
                        continue
                    parts = func_name.split(".")
                    if parts[0] in ALLOWED_DOTTED_PREFIXES:
                        continue

                if func_name in WRONG_FUNCTIONS:
                    suggestion = WRONG_FUNCTIONS[func_name]
                    errors.append(
                        f"BND-013 Unknown function '{func_name}()' in "
                        f"{attr_name} on {t.loc()} — use {suggestion}"
                    )
                else:
                    if (func_name[0].islower()
                            and func_name not in SKIP_FUNCTIONS
                            and "." not in func_name
                            and len(func_name) > 3):
                        errors.append(
                            f"BND-014 Unknown function '{func_name}()' in "
                            f"{attr_name} on {t.loc()} — not an EXPOZY "
                            f"platform function. Use alpineListeners() for "
                            f"API calls or EXPOZY module functions."
                        )
    return errors


def check_platform_state(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    for t in tags:
        xdata = t.get("x-data")
        if xdata and xdata.startswith("{"):
            for vm in _XDATA_VAR_RE.finditer(xdata):
                var_name = vm.group(1)
                if var_name in WRONG_STATE_VARS:
                    correct = WRONG_STATE_VARS[var_name]
                    errors.append(
                        f"BND-015 Wrong state variable '{var_name}' in "
                        f"x-data on {t.loc()} — use '{correct}'"
                    )
        for attr_name, attr_val in t.alpine_attrs():
            if not attr_val:
                continue
            if attr_name in ("x-show", "x-if", "@click", "x-text",
                             ":class", "x-init"):
                for var_name, correct in WRONG_STATE_VARS.items():
                    pattern = r"\b" + re.escape(var_name) + r"\b"
                    if re.search(pattern, attr_val):
                        errors.append(
                            f"BND-016 Wrong state reference '{var_name}' in "
                            f"{attr_name}=\"{attr_val[:60]}\" on {t.loc()} "
                            f"— use '{correct}'"
                        )
    return errors


def check_vanilla_js_in_attrs(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    for t in tags:
        for attr_name, attr_val in t.alpine_attrs():
            if not attr_val:
                continue
            for pat, desc in VANILLA_JS_PATTERNS:
                if pat.search(attr_val):
                    errors.append(
                        f"BND-017 Vanilla JS '{desc}' in {attr_name} on "
                        f"{t.loc()} — use Alpine.js directives instead "
                        f"(x-show, x-bind, :class, etc.)"
                    )
                    break
    return errors


# ============================================================================
# LAYER 4: DATA FIELD ACCURACY
# ============================================================================

def _build_scope_map(tags: List[ParsedTag]) -> Dict[str, Tuple[str, Optional[Set[str]]]]:
    scope: Dict[str, Tuple[str, Optional[Set[str]]]] = {}
    for t in tags:
        xf = t.get("x-for")
        if not xf:
            continue
        m = _FOR_EXPR_RE.search(xf)
        if not m:
            continue
        scope[m.group(1)] = (m.group(2), LOOP_ITEM_FIELDS.get(m.group(2)))
    return scope


def check_data_fields(tags: List[ParsedTag]) -> List[str]:
    errors: List[str] = []
    scope_map = _build_scope_map(tags)

    _STATIC_EXPR_ATTRS = {
        "x-text", "x-html", "x-show", "x-if",
        ":src", ":href", ":class", ":value", ":alt", ":selected",
    }

    SKIP_VARS = {
        "data", "window", "Helpers", "DateHelper", "Math", "Date",
        "Object", "JSON", "console", "String", "Number", "Array",
        "parseInt", "parseFloat", "Boolean", "Error", "undefined",
        "null", "true", "false", "Infinity", "NaN",
    }

    for t in tags:
        for attr_name, attr_val in t.attrs.items():
            if attr_name not in _STATIC_EXPR_ATTRS and not attr_name.startswith(":data-"):
                continue
            for fm in _FIELD_ACCESS_RE.finditer(attr_val):
                var, fld = fm.group(1), fm.group(2)
                if var in SKIP_VARS or var not in scope_map:
                    continue
                source, valid = scope_map[var]
                if valid is None:
                    errors.append(
                        f"FLD-001 '{var}.{fld}' — items from {source} are "
                        f"primitives, not objects — on {t.loc()} {attr_name}"
                    )
                elif fld not in valid:
                    ctx = WRONG_FIELDS_BY_CONTEXT.get(source, {})
                    sug = ctx.get(fld) or KNOWN_WRONG_FIELDS_GENERAL.get(fld)
                    if sug:
                        errors.append(
                            f"FLD-002 Wrong field '{var}.{fld}' — use '{sug}' "
                            f"(endpoint: {source}) on {t.loc()} {attr_name}"
                        )
                    else:
                        errors.append(
                            f"FLD-003 Unknown field '{var}.{fld}' — not in "
                            f"{source} schema on {t.loc()} {attr_name}. "
                            f"Valid: {sorted(valid)}"
                        )
    return errors


# ============================================================================
# LAYER 5: STRUCTURE (warnings + quality checks)
# ============================================================================

def check_images_alt(tags: List[ParsedTag]) -> List[str]:
    return [
        f"STR-001 <img> missing alt (src={t.get('src') or t.get(':src', '?')[:60]})"
        for t in tags
        if t.name == "img" and not t.has("alt") and not t.has(":alt")
    ]


def check_dark_mode(tags: List[ParsedTag]) -> List[str]:
    warnings: List[str] = []
    light_re = re.compile(r"\bbg-(white|gray-\d+|slate-\d+)\b|\btext-(gray-\d+|slate-\d+|black)\b")
    dark_re = re.compile(r"\bdark:")
    layout = {"div", "section", "main", "article", "aside", "header", "footer", "nav", "ul", "li"}
    for t in tags:
        if t.name not in layout:
            continue
        c = t.get("class")
        if c and light_re.search(c) and not dark_re.search(c):
            warnings.append(f"STR-002 {t.loc()} has light bg/text but no dark: variant")
    return warnings


def check_section_wrappers(tags: List[ParsedTag]) -> List[str]:
    root_class = SECTION_WRAPPERS.get("section_root_class", "is-section")
    return [
        f"STR-003 <section> missing {root_class} class {t.loc()}"
        for t in tags
        if t.name == "section" and root_class not in t.get("class", "")
    ]


def check_inline_svg_usage(tags: List[ParsedTag]) -> List[str]:
    svg_count = sum(1 for t in tags if t.name == "svg")
    if svg_count >= 3:
        return [
            f"STR-004 {svg_count} inline <svg> elements found — "
            f"REPLACE with FontAwesome classes (e.g. <i class='fa-solid fa-star'>). "
            f"Inline SVGs waste tokens and risk output truncation."
        ]
    return []


def check_full_document_wrapper(tags: List[ParsedTag]) -> List[str]:
    """STR-010: Catch <html>, <head>, <body> in page templates."""
    doc_tags = {"html", "head", "body"}
    return [
        f"STR-010 Full document element <{t.name}> {t.loc()} — "
        f"output HTML fragment only, remove <!DOCTYPE>/<html>/<head>/<body>"
        for t in tags if t.name in doc_tags
    ]


def check_section_container_pattern(tags: List[ParsedTag]) -> List[str]:
    """STR-011: Catch 'container mx-auto' instead of '!container is-container v2'."""
    errors: List[str] = []
    correct = SECTION_WRAPPERS.get("correct_container", "!container is-container v2")
    wrong_markers_str = SECTION_WRAPPERS.get("wrong_container_markers", "container,mx-auto")
    wrong_markers = [m.strip() for m in wrong_markers_str.split(",")]

    for t in tags:
        cls = t.get("class", "")
        if not cls:
            continue
        if all(m in cls.split() for m in wrong_markers) and "is-container" not in cls:
            errors.append(
                f"STR-011 Wrong container pattern on {t.loc()} — "
                f"use '{correct}' instead of 'container mx-auto'"
            )
    return errors


def check_overlay_layer(tags: List[ParsedTag]) -> List[str]:
    """STR-012: Catch gradient/bg on .is-section root instead of .is-overlay-bg child."""
    errors: List[str] = []
    root_class = SECTION_WRAPPERS.get("section_root_class", "is-section")
    overlay_bg = SECTION_WRAPPERS.get("overlay_bg_class", "is-overlay-bg")
    bg_pattern = re.compile(r"\bbg-(?:gradient|gray-|slate-|amber-|blue-|green-|red-)")

    for t in tags:
        cls = t.get("class", "")
        if not cls:
            continue
        classes = cls.split()
        if root_class in classes and bg_pattern.search(cls) and overlay_bg not in classes:
            errors.append(
                f"STR-012 Background/gradient on {root_class} root {t.loc()} — "
                f"move to .{overlay_bg} child element"
            )
    return errors


def check_dark_mode_contrast(tags: List[ParsedTag]) -> List[str]:
    """STR-013: Catch dark:text-gray-700+ (invisible on dark backgrounds)."""
    warnings: List[str] = []
    invisible_re = re.compile(r"\bdark:text-gray-[789]\d{2}\b")

    for t in tags:
        cls = t.get("class", "")
        if cls and invisible_re.search(cls):
            warnings.append(
                f"STR-013 Dark mode contrast issue on {t.loc()} — "
                f"use dark:text-white or dark:text-slate-300, not dark:text-gray-700+"
            )
    return warnings


def check_cdn_assets(tags: List[ParsedTag]) -> List[str]:
    """STR-014: Catch CDN <script>/<link> tags."""
    errors: List[str] = []
    for t in tags:
        if t.name not in ("script", "link"):
            continue
        src = t.get("src") or t.get("href") or ""
        if src and any(domain in src for domain in CDN_DOMAINS):
            errors.append(
                f"STR-014 CDN asset on {t.loc()} — "
                f"remove external {src[:80]}. Platform loads Tailwind/Alpine globally."
            )
    return errors


# ============================================================================
# LAYER 6: EXPOZY BINDING INTEGRITY
# ============================================================================

def check_duplicate_attributes(parser: _FullParser) -> List[str]:
    """
    STR-020: Detect duplicate HTML attributes on the same element.

    HTML silently drops the first occurrence when an attribute appears twice.
    Especially dangerous for apiData/keyName pairs.
    """
    errors: List[str] = []
    critical_attrs = {"apidata", "keyname", "x-data", "x-for", "x-show", "x-if", "class", "id"}

    for tag_name, attr_name, line in parser.duplicate_attrs:
        if attr_name in critical_attrs:
            errors.append(
                f"STR-020 Duplicate attribute '{attr_name}' on <{tag_name}> "
                f"(line {line}) — HTML silently drops the first value. "
                f"Move the second '{attr_name}' to a nested child element."
            )
        else:
            errors.append(
                f"STR-020 Duplicate attribute '{attr_name}' on <{tag_name}> "
                f"(line {line}) — only the last value is kept by the browser."
            )
    return errors


def check_missing_xdata_for_state(tags: List[ParsedTag]) -> List[str]:
    """
    ALP-020: Detect state variables used in x-show/@click/:class that are
    never declared in any x-data in the document.
    """
    errors: List[str] = []

    LOCAL_STATE_PATTERNS = {
        "tab", "activeTab", "expanded", "open", "isOpen", "openLang",
        "showForm", "show_add_address", "showCalendar", "loading",
        "selectedCalendarId", "selectedDate", "selectedSlot",
        "agreed", "step", "currentSlide",
    }

    declared_vars: Set[str] = set()
    for t in tags:
        xdata = t.get("x-data")
        if xdata and xdata.startswith("{"):
            for vm in _XDATA_VAR_RE.finditer(xdata):
                declared_vars.add(vm.group(1))

    state_ref_attrs = {"x-show", "x-if", "@click", "@click.prevent",
                       ":class", "x-text", "x-init"}
    referenced_vars: Dict[str, Tuple[str, ParsedTag]] = {}

    for t in tags:
        for attr_name, attr_val in t.attrs.items():
            base_attr = attr_name.split(".")[0]
            if base_attr not in state_ref_attrs:
                continue
            if not attr_val:
                continue
            for var in LOCAL_STATE_PATTERNS:
                if re.search(r"\b" + re.escape(var) + r"\b", attr_val):
                    if var not in referenced_vars:
                        referenced_vars[var] = (attr_name, t)

    for var, (attr_name, tag) in referenced_vars.items():
        if var not in declared_vars:
            errors.append(
                f"ALP-020 State variable '{var}' used in {attr_name} on "
                f"{tag.loc()} but never declared in x-data. "
                f"Add x-data=\"{{ {var}: ... }}\" to an ancestor element."
            )

    return errors


def check_missing_input_names(tags: List[ParsedTag]) -> List[str]:
    """
    FRM-001: Detect form inputs missing name attribute when they have value bindings.

    EXPOZY forms collect data via the name attribute. Inputs with :value but
    no name are invisible to the platform's form submission mechanism.
    """
    errors: List[str] = []
    form_elements = {"input", "select", "textarea"}

    for t in tags:
        if t.name not in form_elements:
            continue
        if t.has("name"):
            continue
        if t.has("disabled"):
            continue

        if t.name == "input" and t.get("type") == "hidden":
            if t.has(":value") or t.has("value"):
                errors.append(
                    f"FRM-001 <input type=\"hidden\"> missing name attribute "
                    f"{t.loc()} — platform cannot collect this value"
                )
            continue

        has_binding = (
            t.has(":value") or t.has("x-model") or
            t.has("x-bind:value") or
            (t.name == "select")
        )

        if has_binding:
            errors.append(
                f"FRM-001 <{t.name}> has value binding but no name attribute "
                f"{t.loc()} — alpineListeners() cannot collect this field. "
                f"Add name=\"fieldname\" attribute."
            )

    return errors


def check_keyname_loop_mismatch(tags: List[ParsedTag]) -> List[str]:
    """
    BND-020: Detect x-for loop source that doesn't match any declared keyName.

    The platform stores API responses under the keyName value, so x-for
    loops MUST iterate over data.{keyName}.
    """
    errors: List[str] = []

    keyname_set: Set[str] = set()
    for t in tags:
        kn = t.get("keyname")
        if kn:
            keyname_set.add(kn)

    if not keyname_set:
        return errors

    _NESTED_PATH_PREFIXES = {
        "corePage", "modals", "pageUrl", "screenWidth", "openMobileMenu",
    }

    for t in tags:
        xf = t.get("x-for")
        if not xf:
            continue
        m = _FOR_EXPR_RE.search(xf)
        if not m:
            continue
        src = m.group(2)
        if not src.startswith("data."):
            continue

        parts = src.split(".")
        if len(parts) < 2:
            continue
        data_key = parts[1]

        if data_key in keyname_set:
            continue
        if data_key in _NESTED_PATH_PREFIXES:
            continue

        nearest_kn = None
        nearest_dist = float("inf")
        for other_t in tags:
            other_kn = other_t.get("keyname")
            if not other_kn or other_kn == data_key:
                continue
            dist = abs(other_t.line - t.line)
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_kn = other_kn

        if nearest_kn:
            errors.append(
                f"BND-020 x-for source '{src}' on {t.loc()} — "
                f"'{data_key}' is not a declared keyName on this page. "
                f"Did you mean 'data.{nearest_kn}'? The platform stores "
                f"API responses under the keyName attribute value. "
                f"Declared keyNames: {sorted(keyname_set)}"
            )

    return errors


def check_missing_product_type(tags: List[ParsedTag]) -> List[str]:
    """
    BND-021: Detect Shop.get_orders without data-product_type when keyName != 'orders'.

    Services/digital tabs need data-product_type to filter responses server-side.
    """
    errors: List[str] = []
    for t in tags:
        api = t.get("apidata")
        keyname = t.get("keyname")
        if api != "Shop.get_orders":
            continue
        if keyname and keyname != "orders":
            if not t.has("data-product_type"):
                errors.append(
                    f"BND-021 apiData='Shop.get_orders' with keyName='{keyname}' "
                    f"on {t.loc()} — missing data-product_type attribute. "
                    f"Add data-product_type='4' to filter for services/digital products."
                )
    return errors


def check_xmodel_in_forms(tags: List[ParsedTag]) -> List[str]:
    """
    BND-022: Detect x-model on data.* paths (warning — should use :value + name).

    x-model bypasses EXPOZY's name-based form data collection mechanism.
    """
    warnings: List[str] = []
    form_elements = {"input", "select", "textarea"}

    for t in tags:
        if t.name not in form_elements:
            continue
        xmodel = t.get("x-model")
        if not xmodel:
            continue
        if xmodel.startswith("data."):
            warnings.append(
                f"BND-022 x-model=\"{xmodel[:60]}\" on {t.loc()} — "
                f"use :value=\"{xmodel}\" with name=\"...\" instead. "
                f"x-model bypasses EXPOZY form data collection."
            )
    return warnings


# ============================================================================
# VALIDATION RESULT
# ============================================================================

@dataclass
class ValidationResult:
    """
    Three-category validation result:

    security_errors — BLOCK acceptance. Real XSS/exfiltration vectors.
    quality_errors  — REPORTED but don't block. Correctness issues.
    warnings        — Informational only.
    """
    accepted: bool
    security_errors: List[str] = field(default_factory=list)
    quality_errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def total_errors(self) -> int:
        return len(self.security_errors) + len(self.quality_errors)

    def all_errors(self) -> List[str]:
        """Flat list — used by worker retry feedback loop."""
        return self.security_errors + self.quality_errors

    def all_feedback(self) -> List[str]:
        """Full feedback including warnings — used by retry prompt builder."""
        return self.security_errors + self.quality_errors + self.warnings

    def error_hints(self) -> Dict[str, str]:
        return ERROR_HINTS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "total_errors": self.total_errors,
            "total_warnings": len(self.warnings),
            "security_ok": len(self.security_errors) == 0,
            "quality_ok": len(self.quality_errors) == 0,
            "security_errors": self.security_errors,
            "quality_errors": self.quality_errors,
            "warnings": self.warnings,
        }

    def summary(self) -> str:
        s = "ACCEPTED" if self.accepted else "REJECTED"
        return (
            f"{s}  ({len(self.security_errors)} security, "
            f"{len(self.quality_errors)} quality, "
            f"{len(self.warnings)} warnings)\n"
            f"  Security : {'OK' if not self.security_errors else f'{len(self.security_errors)} BLOCKING'}\n"
            f"  Quality  : {'OK' if not self.quality_errors else f'{len(self.quality_errors)} issues'}\n"
            f"  Warnings : {len(self.warnings)}"
        )


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def validate_html(html: str, page_type: Optional[str] = None) -> ValidationResult:
    """
    Full validation of EXPOZY AI-generated HTML.

    Two-gate model:
      SECURITY (hard gate) — blocks acceptance.
      QUALITY  (soft gate) — reported for LLM retry but doesn't block.

    accepted=True when security_errors is empty.
    """
    parser, tags = parse_html(html)

    # ── SECURITY ERRORS (block acceptance) ────────────────────────────────
    sec = (
        check_forbidden_elements(tags)      # SEC-001
        + check_iframe_sources(tags)        # SEC-004
        + check_unsafe_urls(tags)           # SEC-005
        + check_inline_handlers(tags)       # SEC-006
        + check_css_injection(tags)         # SEC-007
        + check_dangerous_attrs(tags)       # SEC-011
        + check_no_custom_fetch(tags)       # BND-003
        + check_vanilla_js_in_attrs(tags)   # BND-017
    )

    # ── QUALITY ERRORS (reported, don't block) ────────────────────────────
    qual = (
        # HTML syntax
        check_html_syntax(parser, tags)
        # Security-soft
        + check_restricted_elements(tags)   # SEC-002
        + check_element_allowlist(tags)     # SEC-003
        + check_meta_in_body(tags)          # SEC-008
        + check_target_blank(tags)          # SEC-009
        + check_form_action_safety(tags)    # SEC-010
        # Alpine.js compliance
        + check_alpine_directives(tags)
        + check_alpine_listeners(tags)
        + check_xhtml_usage(tags)
        + check_navigation_patterns(tags)
        + check_form_patterns(tags)
        + check_xfor_on_template(tags)
        # EXPOZY data binding
        + check_api_bindings(tags)
        + check_for_loop_sources(tags)
        + check_keyname_values(tags)
        + check_loop_source_paths(tags)
        + check_unknown_functions(tags)
        + check_platform_state(tags)
        # Data field accuracy
        + check_data_fields(tags)
        # Structural quality
        + check_full_document_wrapper(tags)         # STR-010
        + check_section_container_pattern(tags)     # STR-011
        + check_overlay_layer(tags)                 # STR-012
        + check_cdn_assets(tags)                    # STR-014
        + check_inline_svg_usage(tags)              # STR-004
        # Layer 6: Binding integrity
        + check_duplicate_attributes(parser)        # STR-020
        + check_missing_xdata_for_state(tags)       # ALP-020
        + check_missing_input_names(tags)           # FRM-001
        + check_keyname_loop_mismatch(tags)         # BND-020
        + check_missing_product_type(tags)          # BND-021
    )

    # ── WARNINGS (informational only) ─────────────────────────────────────
    wrn = (
        check_images_alt(tags)
        + check_dark_mode(tags)
        + check_section_wrappers(tags)
        + check_dark_mode_contrast(tags)    # STR-013
        + check_xmodel_in_forms(tags)       # BND-022
    )

    ok = not sec

    return ValidationResult(
        accepted=ok,
        security_errors=sec,
        quality_errors=qual,
        warnings=wrn,
    )


# ============================================================================
# BRIDGE — backward-compatible with worker_service.py
# ============================================================================

def validate_template_html(html: str, page_type: Optional[str] = None) -> Dict[str, Any]:
    return validate_html(html, page_type).to_dict()


def validate_template(data: Any = None, page_type: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    if isinstance(data, str):
        return validate_template_html(data, page_type)
    return {"accepted": True, "total_errors": 0, "warnings": ["Legacy JSON — skipped"]}


# ============================================================================
# CLI
# ============================================================================

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python validator.py <template.html> [page_type]")
        sys.exit(1)

    arg = sys.argv[1]
    pt = sys.argv[2] if len(sys.argv) > 2 else None

    if not arg.lstrip().startswith("<"):
        try:
            with open(arg, "r", encoding="utf-8") as fh:
                html_content = fh.read()
        except FileNotFoundError:
            print(f"File not found: {arg}")
            sys.exit(1)
    else:
        html_content = arg

    result = validate_html(html_content, page_type=pt)
    print(result.summary())
    print()
    if result.security_errors:
        print("── Security Errors (BLOCKING) ──")
        for e in result.security_errors:
            print(f"  ✗ {e}")
    if result.quality_errors:
        print("── Quality Errors (non-blocking) ──")
        for e in result.quality_errors:
            print(f"  ○ {e}")
    if result.warnings:
        print("── Warnings ──")
        for w in result.warnings:
            print(f"  ⚠ {w}")

    sys.exit(0 if result.accepted else 1)