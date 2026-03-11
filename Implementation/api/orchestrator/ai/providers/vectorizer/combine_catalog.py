"""
combine_catalog.py
──────────────────
Takes the two separate source files and produces ONE combined JSON.
The vectorizer then chunks this combined file.

Now also auto-generates the _validator section with ALL pre-computed
tables for the HTML validator — so the validator has ZERO hardcoded
catalog data.

The validator reads EVERYTHING from _validator:
  - Alpine directives / events / bindings  (from validation_rules.alpine_js)
  - HTML element allowlist                 (from ai_generation_rules)
  - JS built-in method skip list           (from validation_rules.alpine_js)
  - Iframe domain allowlist                (from validation_rules.iframes)
  - API endpoints, listeners, keyNames     (from component endpoints)
  - Loop sources, field schemas, state     (from component alpine_data)
  - Error hints (flattened code→rule)      (from validator_hints.error_hints)
  - 8 validator config fields              (from validator_hints.*)

To add a new binding like :disabled → edit validation_rules.alpine_js.directives_allowed
→ run combine_catalog.py → validator picks it up. Zero Python changes.

Usage:
    python combine_catalog.py

Input:
    component_catalog.json  ← components, global config, validation, validator_hints
    page_types.json         ← pages, workflow, business context

Output:
    combined_catalog.json   ← everything in one file, cross-referenced + _validator
"""

import json
import re
import sys
from pathlib import Path
from typing import Any, Optional


def load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_page_components(page_def: dict) -> list[str]:
    """
    Walk all section layout patterns and collect every component ID
    referenced by this page.
    """
    found = set()
    for section in page_def.get("sections", []):
        # Pattern 1: direct — "component": "hero_section"
        if "component" in section:
            found.add(section["component"])

        # Pattern 2: columns — left_column/right_column.components[].component
        for col in ("left_column", "right_column"):
            for c in section.get(col, {}).get("components", []):
                if "component" in c:
                    found.add(c["component"])

        # Pattern 3: after_columns[].component
        for ac in section.get("after_columns", []):
            if "component" in ac:
                found.add(ac["component"])

        # Pattern 4: sidebar/content.component (blog listing)
        for key in ("sidebar", "content"):
            sub = section.get(key, {})
            if isinstance(sub, dict) and "component" in sub:
                found.add(sub["component"])

        # Pattern 5: left/right.component (contacts grid)
        for key in ("left", "right"):
            sub = section.get(key, {})
            if isinstance(sub, dict) and "component" in sub:
                found.add(sub["component"])

    return sorted(found)


# =========================================================================
# CATALOG-DRIVEN VALIDATOR CONFIG EXTRACTION
# =========================================================================

def extract_validator_config(catalog: dict) -> dict:
    """
    Extract ALL validation config from the catalog's own rule definitions.

    Reads from:
      - validation_rules.alpine_js.directives_allowed → split into directives/events/bindings
      - ai_generation_rules.output_format.allowed_html_elements → HTML allowlist
      - validation_rules.iframes.allowed_domains → iframe domains
      - validation_rules.alpine_js.js_builtin_methods → JS method skip list
      - validation_rules.alpine_js.helpers_allowed → helper functions
      - validation_rules.alpine_js.navigation_allowed → nav functions

    The validator reads these from _validator with ZERO hardcoded allowlists.
    To add :disabled → edit directives_allowed in JSON → re-run → done.
    """
    vr = catalog.get("validation_rules", {})
    alpine = vr.get("alpine_js", {})
    ai_rules = catalog.get("ai_generation_rules", {})
    output_fmt = ai_rules.get("output_format", {})

    # ── 1. Split directives_allowed into 3 categories ────────────────────
    # The catalog has ONE master list; we categorize by prefix.

    directives_allowed = alpine.get("directives_allowed", [])

    alpine_directives: list[str] = []   # x-data, x-init, x-show, ...
    alpine_events: list[str] = []       # @click, @input, @change, ...
    alpine_bindings: list[str] = []     # :class, :src, :href, :disabled, ...

    seen_d: set[str] = set()
    seen_e: set[str] = set()
    seen_b: set[str] = set()

    for d in directives_allowed:
        base = d.split(".")[0]  # @click.prevent → @click
        if base.startswith("x-"):
            if base not in seen_d:
                seen_d.add(base)
                alpine_directives.append(base)
        elif base.startswith("@"):
            if base not in seen_e:
                seen_e.add(base)
                alpine_events.append(base)
        elif base.startswith(":"):
            if base not in seen_b:
                seen_b.add(base)
                alpine_bindings.append(base)

    # ── 2. HTML element allowlist ────────────────────────────────────────
    catalog_html_elements = output_fmt.get("allowed_html_elements", [])

    # SVG sub-elements (always valid inside <svg>)
    svg_elements = [
        "svg", "path", "circle", "rect", "line", "polyline", "polygon",
        "g", "defs", "clippath", "use", "text", "tspan",
        "lineargradient", "radialgradient", "stop", "mask",
    ]

    # Structural HTML5 elements the catalog might not list explicitly
    structural_elements = [
        "html", "head", "body", "main", "template", "slot",
        "details", "summary", "figure", "figcaption",
        "fieldset", "legend", "datalist", "output", "optgroup",
        "caption", "colgroup", "col", "tfoot",
        "picture", "source", "video", "audio",
        "noscript", "dialog",
        "h4", "h5", "h6", "pre", "blockquote", "code",
        "b", "u", "s", "small", "sub", "sup", "mark",
        "del", "ins", "abbr", "time", "cite", "dfn",
        "kbd", "samp", "var", "wbr", "hr",
        "dl", "dt", "dd",
        "meta", "link", "title",
    ]

    all_html = sorted(set(
        [e.lower() for e in catalog_html_elements]
        + svg_elements
        + structural_elements
    ))

    # ── 3. Forbidden elements (XSS vectors) ──────────────────────────────
    forbidden_elements = ["script", "object", "embed", "base"]

    # ── 4. Iframe domains ────────────────────────────────────────────────
    iframe_config = vr.get("iframes", {})
    allowed_iframe_domains = iframe_config.get("allowed_domains", ["maps.google.com"])

    # ── 5. JS built-in methods (skip list for function checker) ──────────
    # If the catalog defines js_builtin_methods, use that.
    # Otherwise use a comprehensive default list.
    js_builtin_methods = alpine.get("js_builtin_methods", [
        # Array methods
        "find", "filter", "map", "reduce", "forEach", "some", "every",
        "includes", "indexOf", "slice", "join", "split", "trim",
        "replace", "match", "push", "pop", "shift", "concat",
        "sort", "reverse", "flat", "flatMap", "fill", "keys", "values",
        "entries", "at", "findIndex",
        # String methods
        "toString", "toLowerCase", "toUpperCase", "startsWith", "endsWith",
        "substring", "charAt", "padStart", "padEnd", "repeat",
        "charCodeAt", "normalize", "localeCompare",
        # Object/utility
        "hasOwnProperty", "valueOf",
        # Number
        "toFixed", "toPrecision",
        # Date instance methods (used in legal pages: new Date().toLocaleDateString())
        "toLocaleDateString", "toLocaleTimeString", "toLocaleString",
        "toISOString", "toDateString", "toTimeString", "toUTCString",
        "getFullYear", "getMonth", "getDate", "getDay",
        "getHours", "getMinutes", "getSeconds", "getTime",
        "setFullYear", "setMonth", "setDate",
    ])

    # ── 6. Helper / navigation functions ─────────────────────────────────
    helpers = alpine.get("helpers_allowed", [])
    nav = alpine.get("navigation_allowed", [])

    helper_funcs: list[str] = []
    seen_h: set[str] = set()
    for h in helpers + nav:
        m = re.match(r"([\w.$]+)\s*\(", h)
        if m and m.group(1) not in seen_h:
            seen_h.add(m.group(1))
            helper_funcs.append(m.group(1))

    # ── 7. Required attributes per element ───────────────────────────────
    required_element_attrs = {
        "img": ["alt"],
    }

    return {
        "allowed_alpine_directives": sorted(set(alpine_directives)),
        "allowed_alpine_events": sorted(set(alpine_events)),
        "allowed_alpine_bindings": sorted(set(alpine_bindings)),
        "allowed_html_elements": all_html,
        "forbidden_html_elements": forbidden_elements,
        "allowed_iframe_domains": allowed_iframe_domains,
        "js_builtin_methods": sorted(set(js_builtin_methods)),
        "helper_functions": sorted(set(helper_funcs)),
        "required_element_attrs": required_element_attrs,
    }


# =========================================================================
# VALIDATOR TABLE EXTRACTION
# =========================================================================

# Regex to pull listener names from alpineListeners('...') patterns
_LISTENER_EXTRACT_RE = re.compile(r"alpineListeners\s*\(\s*['\"]([^'\"]+)['\"]")


def _extract_return_fields(returns: Any) -> list[str]:
    """
    Extract field names from an endpoint returns schema.
    Handles nested objects and array items.
    """
    fields: set[str] = set()

    if isinstance(returns, list):
        # Array of objects — fields come from the first item
        if returns and isinstance(returns[0], dict):
            for key, value in returns[0].items():
                fields.add(key)
                if isinstance(value, dict):
                    for sub_key in value:
                        if sub_key != "type":
                            fields.add(f"{key}.{sub_key}")
                elif isinstance(value, list) and value and isinstance(value[0], dict):
                    for sub_key in value[0]:
                        fields.add(f"{key}[].{sub_key}")
        return sorted(fields)

    if not isinstance(returns, dict):
        return []

    for key, value in returns.items():
        fields.add(key)
        if isinstance(value, str):
            continue  # Leaf: "title": "string"
        if isinstance(value, dict):
            # Could be nested object or typed field
            if "type" in value and len(value) <= 2:
                continue  # Simple typed field
            for sub_key in value:
                if sub_key != "type":
                    fields.add(f"{key}.{sub_key}")
        elif isinstance(value, list):
            if value and isinstance(value[0], dict):
                for sub_key in value[0]:
                    fields.add(f"{key}[].{sub_key}")
            elif value and isinstance(value[0], str):
                fields.add(key)  # Array of primitives

    return sorted(fields)


def _extract_result_item_fields(returns: Any) -> Optional[list[str]]:
    """
    EXPOZY result-envelope unwrapper.

    EXPOZY APIs return {"result": [...items...]}. Alpine's apiData system
    unwraps this so that data.{keyName} contains the result array directly.
    When the AI iterates with x-for="item in data.{keyName}", each item
    is an inner object from result[].

    This function extracts the INNER object fields (id, title, link, etc.)
    rather than the envelope fields (result, result[].id, etc.).

    Returns None if the returns schema doesn't have a result-envelope pattern.
    """
    if not isinstance(returns, dict):
        return None
    result = returns.get("result")
    if isinstance(result, list) and result and isinstance(result[0], dict):
        # Found result-envelope: extract inner object fields
        return _extract_return_fields(result)
    return None


def _walk_for_listeners(obj: Any) -> set[str]:
    """
    Recursively walk any dict/list structure looking for
    alpineListeners('...') patterns in string values.
    """
    found: set[str] = set()
    if isinstance(obj, str):
        for m in _LISTENER_EXTRACT_RE.finditer(obj):
            found.add(m.group(1))
    elif isinstance(obj, dict):
        for v in obj.values():
            found |= _walk_for_listeners(v)
    elif isinstance(obj, list):
        for item in obj:
            found |= _walk_for_listeners(item)
    return found


def _flatten_error_hints(raw_hints: dict) -> dict:
    """
    Transform structured error_hints into flat {code: rule} map.

    Input (from component_catalog.json validator_hints.error_hints):
        {
            "no_script_tags": {
                "code": "SEC-001",
                "rule": "Remove all <script> tags...",
                "wrong": "<script>...</script>",
                "correct": "<div x-data>...</div>",
                "applies_to": ["all templates"]
            },
            ...
        }

    Output (for _validator.error_hints → validator_v2.py reads flat):
        {
            "SEC-001": "Remove all <script> tags...",
            ...
        }

    The structured format stays in component_catalog.json for human
    readability and for the AI prompt (wrong/correct examples).
    The flat format is what the validator runtime needs (code → fix text).
    """
    flat: dict[str, str] = {}
    if not isinstance(raw_hints, dict):
        return flat
    for key, entry in raw_hints.items():
        if isinstance(entry, dict) and "code" in entry and "rule" in entry:
            flat[entry["code"]] = entry["rule"]
        elif isinstance(entry, str):
            # Already flat (legacy format: "SEC-001": "Remove...")
            flat[key] = entry
    return flat


def build_validator_tables(catalog: dict, pages: dict) -> dict:
    """
    Walk the entire catalog and auto-extract every table the validator needs.

    Reads from ACTUAL catalog field names:
      - components[].endpoints[].ref           → API endpoints
      - components[].endpoints[].key_name      → keyName mapping
      - components[].endpoints[].binding       → listener vs apiData
      - components[].endpoints[].returns       → field schemas
      - components[].alpine_actions            → listeners + functions
      - components[].alpine_data               → state vars + loop sources
      - components[].sub_components            → inline listeners from patterns
      - components[].controls.trigger_method   → listeners
      - validation_rules.alpine_js             → authoritative allowed lists
      - page_types[].dataSources[]             → additional endpoints
      - page_types[].actions[]                 → additional listeners
      - global_types[].dataSources[]           → additional endpoints
      - validator_hints                        → WRONG_* correction maps +
                                                 error_hints (flattened) +
                                                 8 config fields (passthrough)

    Returns a dict ready to embed as combined["_validator"].
    """
    allowed_api_endpoints: set[str] = set()
    implicit_keyname_endpoints: set[str] = set()
    endpoint_keyname_map: dict[str, str] = {}
    allowed_listeners: set[str] = set()
    allowed_loop_sources: set[str] = set()
    allowed_functions: set[str] = set()
    allowed_state_vars: set[str] = set()
    loop_item_fields: dict[str, Optional[list[str]]] = {}

    components = catalog.get("components", {})

    # ── Extract from each component ──────────────────────────────────────
    for comp_id, comp in components.items():
        if not isinstance(comp, dict):
            continue

        # ── Endpoints ────────────────────────────────────────────────────
        for ep in comp.get("endpoints", []):
            if not isinstance(ep, dict):
                continue

            # Field name fix: catalog uses "ref" not "apiData"
            api_data = ep.get("apiData") or ep.get("ref", "")
            # Field name fix: catalog uses "key_name" not "keyName"
            key_name = ep.get("keyName") or ep.get("key_name", "")
            binding = (ep.get("binding") or "").lower()

            if api_data:
                allowed_api_endpoints.add(api_data)

            if api_data and key_name:
                endpoint_keyname_map[api_data] = key_name

            if api_data and ep.get("implicit_keyname"):
                implicit_keyname_endpoints.add(api_data)

            # If binding is alpineListeners, the ref is also a listener
            if binding == "alpinelisteners" and api_data:
                allowed_listeners.add(api_data)

            # Loop sources — derive from keyName
            if key_name:
                allowed_loop_sources.add(f"data.{key_name}")
                allowed_loop_sources.add(f"data.{key_name}.result")

            # Explicit loop_source / data_path
            loop_src = ep.get("loop_source") or ep.get("data_path")
            sources: list[str] = []
            if isinstance(loop_src, str):
                sources = [loop_src]
            elif isinstance(loop_src, list):
                sources = [s for s in loop_src if isinstance(s, str)]
            for src in sources:
                allowed_loop_sources.add(src)

            # ── Field schemas from returns ───────────────────────────────
            returns = ep.get("returns")
            if returns:
                # Full envelope fields (for explicit data_path sources)
                fields = _extract_return_fields(returns)
                # Inner result[] item fields (for data.{keyName} iteration)
                inner_fields = _extract_result_item_fields(returns)

                if fields:
                    for src in sources:
                        loop_item_fields[src] = fields

                if key_name:
                    effective = inner_fields if inner_fields else fields
                    if effective:
                        loop_item_fields.setdefault(f"data.{key_name}", effective)
                    if effective:
                        loop_item_fields.setdefault(f"data.{key_name}.result", effective)

            # Extract listeners from submit_pattern, success_callback strings
            for pat_key in ("submit_pattern", "success_callback"):
                pat_val = ep.get(pat_key, "")
                if isinstance(pat_val, str):
                    for m in _LISTENER_EXTRACT_RE.finditer(pat_val):
                        allowed_listeners.add(m.group(1))

        # ── Also extract keyName from structure.required_attributes ──────
        struct = comp.get("structure", {})
        if isinstance(struct, dict):
            req_attrs = struct.get("required_attributes", {})
            if isinstance(req_attrs, dict):
                kn = req_attrs.get("keyName") or req_attrs.get("keyname", "")
                api = req_attrs.get("apiData") or req_attrs.get("apidata", "")
                if api:
                    allowed_api_endpoints.add(api)
                if api and kn:
                    endpoint_keyname_map.setdefault(api, kn)
                if kn:
                    allowed_loop_sources.add(f"data.{kn}")
                    allowed_loop_sources.add(f"data.{kn}.result")

        # ── Alpine actions → listeners + functions ───────────────────────
        alpine_actions = comp.get("alpine_actions", {})
        if isinstance(alpine_actions, dict):
            for action_name in alpine_actions:
                allowed_listeners.add(action_name)
                allowed_functions.add(action_name)

        # ── Alpine data → state vars + loop sources ──────────────────────
        alpine_data = comp.get("alpine_data", {})
        if isinstance(alpine_data, dict):
            for key, val in alpine_data.items():
                if key in ("local_scope", "platform_state"):
                    if isinstance(val, dict):
                        for var_name in val:
                            if var_name.startswith("data."):
                                allowed_state_vars.add(var_name)
                                allowed_loop_sources.add(var_name)
                            else:
                                allowed_state_vars.add(var_name)
                else:
                    if key.startswith("data."):
                        allowed_state_vars.add(key)
                        allowed_loop_sources.add(key)
                    else:
                        allowed_state_vars.add(key)

        # ── data_source string → extract cross-component keyName ─────────
        data_source = comp.get("data_source", "")
        if isinstance(data_source, str):
            for m in re.finditer(r"(get\.\w+|post\.\w+|put\.\w+|delete\.\w+)", data_source):
                ref = m.group(1)
                allowed_api_endpoints.add(ref)
            for m in re.finditer(r"data\.(\w+)", data_source):
                key = m.group(1)
                allowed_loop_sources.add(f"data.{key}")
                allowed_state_vars.add(f"data.{key}")

        # ── Auto-fetched data → loop sources ─────────────────────────────
        auto_fetched = comp.get("auto_fetched_data", {})
        if isinstance(auto_fetched, dict):
            for af_key, af_val in auto_fetched.items():
                if isinstance(af_val, dict):
                    kn = af_val.get("key_name", "")
                    dp = af_val.get("data_path", "")
                    trigger = af_val.get("trigger", "")
                    if kn:
                        allowed_loop_sources.add(f"data.{kn}")
                        allowed_loop_sources.add(f"data.{kn}.result")
                        allowed_state_vars.add(f"data.{kn}")
                    if dp:
                        allowed_loop_sources.add(dp)
                    for m in _LISTENER_EXTRACT_RE.finditer(trigger):
                        allowed_listeners.add(m.group(1))

        # ── Controls → listeners ─────────────────────────────────────────
        controls = comp.get("controls", {})
        if isinstance(controls, dict):
            trigger = controls.get("trigger_method", "")
            if isinstance(trigger, str):
                for m in _LISTENER_EXTRACT_RE.finditer(trigger):
                    allowed_listeners.add(m.group(1))

        # ── Sub-components → scan for listeners in all string values ─────
        sub_comps = comp.get("sub_components", {})
        if isinstance(sub_comps, dict):
            allowed_listeners |= _walk_for_listeners(sub_comps)

        # ── runtime_interactions (future-proof if schema changes) ────────
        rt = comp.get("runtime_interactions", {})
        if isinstance(rt, dict):
            for listener in rt.get("alpine_listeners", []):
                if isinstance(listener, str):
                    allowed_listeners.add(listener)
            state = rt.get("alpine_state", {})
            if isinstance(state, dict):
                for var_name in state:
                    allowed_state_vars.add(var_name)
            for fn in rt.get("platform_functions", []):
                if isinstance(fn, str):
                    allowed_functions.add(fn)

    # ── Cross-component field schema propagation ─────────────────────────
    _endpoint_returns_cache: dict[str, Any] = {}
    for comp_id, comp in components.items():
        if not isinstance(comp, dict):
            continue
        for ep in comp.get("endpoints", []):
            if not isinstance(ep, dict):
                continue
            ref = ep.get("ref", "")
            returns = ep.get("returns")
            if ref and returns:
                _endpoint_returns_cache[ref] = returns

    for src in list(allowed_loop_sources):
        if src in loop_item_fields:
            continue
        if src.startswith("data."):
            key = src[5:]
            for ep_ref, ep_kn in endpoint_keyname_map.items():
                if ep_kn == key and ep_ref in _endpoint_returns_cache:
                    returns = _endpoint_returns_cache[ep_ref]
                    inner = _extract_result_item_fields(returns)
                    fields = inner if inner else _extract_return_fields(returns)
                    if fields:
                        loop_item_fields[src] = fields
                        loop_item_fields.setdefault(f"{src}.result", fields)
                    break
            else:
                guess_ref = f"get.{key}"
                if guess_ref in _endpoint_returns_cache:
                    returns = _endpoint_returns_cache[guess_ref]
                    inner = _extract_result_item_fields(returns)
                    fields = inner if inner else _extract_return_fields(returns)
                    if fields:
                        loop_item_fields[src] = fields
                        loop_item_fields.setdefault(f"{src}.result", fields)

    # ── Extract from validation_rules.alpine_js (authoritative lists) ────
    val_rules = catalog.get("validation_rules", {})
    alpine_js = val_rules.get("alpine_js", {})

    for listener in alpine_js.get("listeners_allowed", []):
        if isinstance(listener, str):
            allowed_listeners.add(listener)

    for helper in alpine_js.get("helpers_allowed", []):
        if isinstance(helper, str):
            fn_name = helper.split("(")[0].strip()
            if fn_name:
                allowed_functions.add(fn_name)

    for nav in alpine_js.get("navigation_allowed", []):
        if isinstance(nav, str):
            fn_name = nav.split("(")[0].strip()
            if fn_name:
                allowed_functions.add(fn_name)

    # ── Extract from page_types ──────────────────────────────────────────
    page_types = pages.get("page_types", {})
    for page_id, page_def in page_types.items():
        if not isinstance(page_def, dict):
            continue

        for ds in page_def.get("dataSources", []):
            if not isinstance(ds, dict):
                continue
            method = ds.get("method", "") or ds.get("apiData", "") or ds.get("ref", "")
            key_name = ds.get("keyName") or ds.get("key_name", "")
            if method:
                allowed_api_endpoints.add(method)
            if method and key_name:
                endpoint_keyname_map[method] = key_name
            if key_name:
                allowed_loop_sources.add(f"data.{key_name}")
                allowed_loop_sources.add(f"data.{key_name}.result")

            loop_src = ds.get("loop_source") or ds.get("data_path")
            if isinstance(loop_src, str):
                allowed_loop_sources.add(loop_src)
            elif isinstance(loop_src, list):
                for s in loop_src:
                    if isinstance(s, str):
                        allowed_loop_sources.add(s)

        for ep in page_def.get("page_endpoints", []):
            if not isinstance(ep, dict):
                continue
            api = ep.get("apiData") or ep.get("ref", "")
            kn = ep.get("keyName") or ep.get("key_name", "")
            if api:
                allowed_api_endpoints.add(api)
            if api and kn:
                endpoint_keyname_map[api] = kn
            if kn:
                allowed_loop_sources.add(f"data.{kn}")
                allowed_loop_sources.add(f"data.{kn}.result")

        for action in page_def.get("actions", []):
            if isinstance(action, dict):
                method = action.get("method", "")
                if method:
                    allowed_listeners.add(method)

    # ── Extract from global_types ────────────────────────────────────────
    global_types = pages.get("global_types", {})
    for gt_id, gt_def in global_types.items():
        if not isinstance(gt_def, dict):
            continue
        for ds in gt_def.get("dataSources", []):
            if not isinstance(ds, dict):
                continue
            method = ds.get("method", "") or ds.get("apiData", "") or ds.get("ref", "")
            key_name = ds.get("keyName") or ds.get("key_name", "")
            if method:
                allowed_api_endpoints.add(method)
            if method and key_name:
                endpoint_keyname_map[method] = key_name

    # =====================================================================
    # VALIDATOR HINTS — correction maps + error hints + 8 config fields
    # =====================================================================
    hints = catalog.get("validator_hints", {})

    # ── Extract catalog-driven config (Alpine/HTML/JS/iframe allowlists) ─
    config = extract_validator_config(catalog)

    # =====================================================================
    # FLATTEN ERROR_HINTS: structured → {code: rule}
    # =====================================================================
    # component_catalog.json stores error_hints as descriptive objects:
    #   {"no_script_tags": {"code": "SEC-001", "rule": "Remove...", ...}}
    # validator_v2.py reads them as flat:
    #   {"SEC-001": "Remove..."}
    # This transform bridges the two formats.
    # =====================================================================
    flat_error_hints = _flatten_error_hints(hints.get("error_hints", {}))

    # ── Build final output ───────────────────────────────────────────────
    tables = {
        "_description": (
            "Auto-generated by combine_catalog.py. "
            "Do NOT edit — update component_catalog.json or page_types.json instead."
        ),

        # ── Catalog-driven config (replaces hardcoded sets in validator) ──
        "allowed_alpine_directives": config["allowed_alpine_directives"],
        "allowed_alpine_events": config["allowed_alpine_events"],
        "allowed_alpine_bindings": config["allowed_alpine_bindings"],
        "allowed_html_elements": config["allowed_html_elements"],
        "forbidden_html_elements": config["forbidden_html_elements"],
        "allowed_iframe_domains": config["allowed_iframe_domains"],
        "js_builtin_methods": config["js_builtin_methods"],
        "helper_functions": config["helper_functions"],
        "required_element_attrs": config["required_element_attrs"],

        # ── Auto-extracted from components + pages (existing tables) ──────
        "allowed_api_endpoints": sorted(allowed_api_endpoints),
        "implicit_keyname_endpoints": sorted(implicit_keyname_endpoints),
        "endpoint_keyname_map": dict(sorted(endpoint_keyname_map.items())),
        "allowed_listeners": sorted(allowed_listeners),
        "allowed_loop_sources": sorted(allowed_loop_sources),
        "allowed_functions": sorted(allowed_functions),
        "allowed_state_vars": sorted(allowed_state_vars),
        "loop_item_fields": {
            k: v for k, v in sorted(loop_item_fields.items())
        },

        # ── ERROR HINTS: flattened from structured → {code: rule} ────────
        # Input:  {"no_script_tags": {"code": "SEC-001", "rule": "Remove..."}}
        # Output: {"SEC-001": "Remove..."}
        "error_hints": flat_error_hints,

        # ── WRONG_* correction maps (manually curated, passthrough) ──────
        "wrong_loop_sources": hints.get("wrong_loop_sources", {}),
        "wrong_functions": hints.get("wrong_functions", {}),
        "wrong_state_vars": hints.get("wrong_state_vars", {}),
        "wrong_fields_by_context": hints.get("wrong_fields_by_context", {}),
        "known_wrong_fields_general": hints.get("known_wrong_fields_general", {}),

        # =================================================================
        # 8 VALIDATOR CONFIG FIELDS — passthrough from validator_hints
        # =================================================================
        # These were previously hardcoded in validator.py. Now they live in
        # component_catalog.json → validator_hints and are passed through
        # to _validator for validator_v2.py to load at startup.
        #
        # To update: edit validator_hints.cdn_domains in component_catalog.json
        # → run combine_catalog.py → restart worker. Zero Python changes.
        #
        # validator_v2.py reads:
        #   RESTRICTED_ELEMENTS   = _to_dict(_V.get("restricted_html_elements"))
        #   CDN_DOMAINS           = _to_list(_V.get("cdn_domains"))
        #   SHADOW_DATA_VARS      = _to_set(_V.get("shadow_data_vars"))
        #   SKIP_FUNCTIONS        = _to_set(_V.get("skip_functions"))
        #   ALLOWED_DOTTED_PREFIXES = _to_set(_V.get("allowed_dotted_prefixes"))
        #   _DIRECT_CALL_RE       = compiled from _V.get("direct_call_modules")
        #   VANILLA_JS_PATTERNS   = compiled from _V.get("vanilla_js_patterns")
        #   SECTION_WRAPPERS      = _to_dict(_V.get("section_wrappers"))
        # =================================================================
        "restricted_html_elements": hints.get("restricted_html_elements", {}),
        "cdn_domains":              hints.get("cdn_domains", []),
        "shadow_data_vars":         hints.get("shadow_data_vars", []),
        "skip_functions":           hints.get("skip_functions", []),
        "allowed_dotted_prefixes":  hints.get("allowed_dotted_prefixes", []),
        "direct_call_modules":      hints.get("direct_call_modules", []),
        "vanilla_js_patterns":      hints.get("vanilla_js_patterns", []),
        "section_wrappers":         hints.get("section_wrappers", {}),
    }

    return tables


# =========================================================================
# MAIN COMBINE LOGIC
# =========================================================================

def combine(catalog_path: str, pages_path: str) -> dict:
    catalog = load(catalog_path)
    pages = load(pages_path)

    components = catalog.get("components", {})

    # ── Build combined document ──────────────────────────────────────────
    combined = {
        "meta": {
            "catalog_version": catalog.get("catalog_version"),
            "pages_schema_version": pages.get("schema_version"),
            "platform": catalog.get("platform", "EXPOZY"),
            "description": (
                "Combined EXPOZY catalog. Auto-generated from "
                "component_catalog.json + page_types.json. "
                "Do NOT edit — edit the source files instead."
            ),
        },

        # ── From component_catalog.json ──
        "ai_generation_rules": catalog.get("ai_generation_rules", {}),
        "global": catalog.get("global", {}),
        "components": components,

        # ── From page_types.json ──
        "generation_workflow": pages.get("generation_workflow", {}),
        "business_context": pages.get("business_context", {}),
        "page_types": pages.get("page_types", {}),
        "global_types": pages.get("global_types", {}),
        "output_manifest": pages.get("output_manifest", {}),

        # ── Merged from both ──
        "validation_rules": {
            **catalog.get("validation_rules", {}),
            "generation_checks": (
                pages.get("validation_rules", {}).get("checks", [])
            ),
        },

        # ── Auto-generated validator tables ──
        "_validator": build_validator_tables(catalog, pages),
    }

    # ── Cross-reference: resolve which components each page uses ─────────
    for page_id, page_def in combined["page_types"].items():
        comp_ids = resolve_page_components(page_def)
        page_def["_resolved_components"] = comp_ids

    # ── Cross-reference: resolve global type components ──────────────────
    for gt_id, gt_def in combined.get("global_types", {}).items():
        cid = gt_def.get("component")
        if cid and cid in components:
            gt_def["_resolved_component"] = cid

    # ── Validate: all refs point to real components ──────────────────────
    valid_ids = set(components.keys())
    all_refs = set()
    for page_def in combined["page_types"].values():
        all_refs.update(page_def.get("_resolved_components", []))
    for gt_def in combined.get("global_types", {}).values():
        cid = gt_def.get("component")
        if cid:
            all_refs.add(cid)

    missing = all_refs - valid_ids
    if missing:
        print(f"\u26a0\ufe0f  WARNING: Component refs not found in catalog: {missing}",
              file=sys.stderr)

    return combined


def main():
    catalog_path = sys.argv[1] if len(sys.argv) > 1 else "component_catalog.json"
    pages_path = sys.argv[2] if len(sys.argv) > 2 else "page_types.json"
    output_path = sys.argv[3] if len(sys.argv) > 3 else "combined_catalog.json"

    combined = combine(catalog_path, pages_path)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

    # ── Summary ──────────────────────────────────────────────────────────
    n_comps = len(combined["components"])
    n_pages = len(combined["page_types"])
    n_gt = len(combined.get("global_types", {}))
    v = combined["_validator"]

    print(f"\u2705 Combined catalog written to {output_path}")
    print(f"   Components: {n_comps}")
    print(f"   Page types: {n_pages}")
    print(f"   Global types: {n_gt}")
    print()

    print(f"   _validator tables (auto-extracted):")
    print(f"     Endpoints:          {len(v['allowed_api_endpoints'])}")
    print(f"     KeyName map:        {len(v['endpoint_keyname_map'])}")
    print(f"     Listeners:          {len(v['allowed_listeners'])}")
    print(f"     Loop sources:       {len(v['allowed_loop_sources'])}")
    print(f"     Functions:          {len(v['allowed_functions'])}")
    print(f"     State vars:         {len(v['allowed_state_vars'])}")
    print(f"     Field schemas:      {len(v['loop_item_fields'])}")
    print(f"     Wrong loop sources: {len(v['wrong_loop_sources'])}")
    print(f"     Wrong functions:    {len(v['wrong_functions'])}")
    print(f"     Wrong state vars:   {len(v['wrong_state_vars'])}")
    print(f"     Wrong fields ctx:   {len(v['wrong_fields_by_context'])}")
    print(f"     Error hints:        {len(v.get('error_hints', {}))}")
    print()

    print(f"   _validator config (catalog-driven):")
    print(f"     Alpine directives:  {len(v['allowed_alpine_directives'])}")
    print(f"     Alpine events:      {len(v['allowed_alpine_events'])}")
    print(f"     Alpine bindings:    {len(v['allowed_alpine_bindings'])}")
    print(f"     HTML elements:      {len(v['allowed_html_elements'])}")
    print(f"     Forbidden elements: {len(v['forbidden_html_elements'])}")
    print(f"     Iframe domains:     {len(v['allowed_iframe_domains'])}")
    print(f"     JS builtins:        {len(v['js_builtin_methods'])}")
    print(f"     Helper functions:   {len(v['helper_functions'])}")
    print()

    print(f"   _validator hints config (from validator_hints):")
    print(f"     Restricted elements:  {len(v.get('restricted_html_elements', {}))}")
    print(f"     CDN domains:          {len(v.get('cdn_domains', []))}")
    print(f"     Shadow data vars:     {len(v.get('shadow_data_vars', []))}")
    print(f"     Skip functions:       {len(v.get('skip_functions', []))}")
    print(f"     Dotted prefixes:      {len(v.get('allowed_dotted_prefixes', []))}")
    print(f"     Direct-call modules:  {len(v.get('direct_call_modules', []))}")
    print(f"     Vanilla JS patterns:  {len(v.get('vanilla_js_patterns', []))}")
    print(f"     Section wrappers:     {len(v.get('section_wrappers', {}))}")
    print()

    for pid, pdef in combined["page_types"].items():
        comps = pdef.get("_resolved_components", [])
        print(f"   {pid}: refs \u2192 {comps}")

    for gtid, gtdef in combined.get("global_types", {}).items():
        ref = gtdef.get("_resolved_component", gtdef.get("component"))
        print(f"   {gtid} (global): ref \u2192 [{ref}]")


if __name__ == "__main__":
    main()