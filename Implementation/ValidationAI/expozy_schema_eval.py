"""
EXPOZY Template Validator (with Ajv)

Validates AI-generated JSON templates using:
- Ajv (Node.js) for JSON Schema validation
- Python for semantic/security checks

Requirements:
- Node.js installed
- npm install ajv ajv-formats (in same directory)
- ajv_validate.mjs (in same directory)
- schemas/expozy.schema.json

Usage:
    from validator import validate
    result = validate(template_dict)
"""

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Iterable, Optional, Tuple


# =============================================================================
# CONFIG
# =============================================================================

ROOT = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(ROOT, "schemas", "expozy.schema.json")
AJV_RUNNER = os.path.join(ROOT, "ajv_validate.mjs")
NODE = "node"


# =============================================================================
# TRAVERSAL HELPERS
# =============================================================================

def meta_of(pkg: Dict[str, Any]) -> Dict[str, Any]:
    m = pkg.get("metadata") or pkg.get("meta")
    return m if isinstance(m, dict) else {}


def _sections_base_and_list(pkg: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    secs = pkg.get("sections")
    if isinstance(secs, list):
        return "$.sections", [s for s in secs if isinstance(s, dict)]
    layout = pkg.get("layout")
    if isinstance(layout, dict):
        secs2 = layout.get("sections")
        if isinstance(secs2, list):
            return "$.layout.sections", [s for s in secs2 if isinstance(s, dict)]
    return "$.layout.sections", []


def walk_components(comp: Any, path: str) -> Iterable[Tuple[str, Dict[str, Any]]]:
    if not isinstance(comp, dict):
        return
    yield (path, comp)
    kids = comp.get("children")
    if isinstance(kids, list):
        for i, k in enumerate(kids):
            yield from walk_components(k, f"{path}.children[{i}]")


def all_components(pkg: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    base, secs = _sections_base_and_list(pkg)
    for si, sec in enumerate(secs):
        yield from walk_components(sec, f"{base}[{si}]")


# =============================================================================
# AJV SCHEMA VALIDATION
# =============================================================================

def ajv_check(data: Dict[str, Any], schema_path: str = SCHEMA_PATH) -> List[str]:
    """
    Validate data against JSON Schema using Ajv (Node.js).
    Returns list of error strings (empty if valid).
    """
    if not os.path.exists(AJV_RUNNER):
        return [f"Missing: {AJV_RUNNER}"]
    if not os.path.exists(schema_path):
        return [f"Missing: {schema_path}"]

    # Write data to temp file
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="expozy_data_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        # Run Ajv
        result = subprocess.run(
            [NODE, AJV_RUNNER, schema_path, tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return [f"Node.js not found ('{NODE}'). Install Node.js and add to PATH."]
    except subprocess.TimeoutExpired:
        return ["Ajv validation timed out"]
    except Exception as e:
        return [f"Ajv subprocess error: {e}"]
    finally:
        # Always clean up temp file
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # Parse Ajv output
    stdout = (result.stdout or "").strip()
    if not stdout:
        stderr = (result.stderr or "").strip()
        return [f"Ajv no output. stderr: {stderr[:200] if stderr else '<empty>'}"]

    try:
        out = json.loads(stdout)
    except json.JSONDecodeError:
        return [f"Ajv output not JSON: {stdout[:200]}"]

    if out.get("valid") is True:
        return []

    if out.get("fatal") is True:
        errs = out.get("errors") or []
        if errs and isinstance(errs[0], dict):
            return [f"AJV_FATAL: {errs[0].get('message', 'unknown')}"]
        return ["AJV_FATAL: unknown error"]

    # Format errors
    errors: List[str] = []
    for e in (out.get("errors") or []):
        if not isinstance(e, dict):
            continue
        ip = e.get("instancePath") or ""
        msg = e.get("message") or "schema error"
        kw = e.get("keyword") or ""
        
        path = "$"
        if ip:
            path = "$" + ip.replace("/", ".")
        
        errors.append(f"{path}: {msg} ({kw})")
    
    return errors if errors else ["Schema validation failed"]


# =============================================================================
# ENDPOINT CHECK
# =============================================================================

API_RE = re.compile(r"^(get|post|put|patch|delete)\.[a-z][a-z0-9_]*$")
MOD_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*\.[a-z][a-z0-9_]*$")
BAD_EP = re.compile(r"(drop|truncate|delete\.users|delete\.all|admin|exec|eval|system)", re.I)


def check_endpoints(pkg: Dict[str, Any], allowed: Optional[Set[str]] = None) -> List[str]:
    errs: List[str] = []

    def check(ep: str, path: str):
        if not (API_RE.match(ep) or MOD_RE.match(ep)):
            errs.append(f"{path}: '{ep}' invalid format")
        if BAD_EP.search(ep):
            errs.append(f"{path}: '{ep}' dangerous")
        if allowed and ep not in allowed:
            errs.append(f"{path}: '{ep}' not in allowlist")

    for i, ds in enumerate(pkg.get("dataSources") or []):
        if isinstance(ds, dict):
            ep = ds.get("endpoint") or ds.get("method")
            if isinstance(ep, str):
                check(ep, f"$.dataSources[{i}].endpoint")

    for i, ac in enumerate(pkg.get("actions") or []):
        if isinstance(ac, dict):
            ep = ac.get("endpoint") or ac.get("method")
            if isinstance(ep, str):
                check(ep, f"$.actions[{i}].endpoint")

    return errs


# =============================================================================
# CROSS-REFERENCES
# =============================================================================

def check_refs(pkg: Dict[str, Any]) -> List[str]:
    errs: List[str] = []

    action_ids = {a["id"] for a in (pkg.get("actions") or []) 
                  if isinstance(a, dict) and isinstance(a.get("id"), str)}
    ds_ids = {d["id"] for d in (pkg.get("dataSources") or []) 
              if isinstance(d, dict) and isinstance(d.get("id"), str)}

    for path, comp in all_components(pkg):
        acts = comp.get("actions")
        if isinstance(acts, list):
            for j, aid in enumerate(acts):
                if isinstance(aid, str) and aid not in action_ids:
                    errs.append(f"{path}.actions[{j}]: unknown action '{aid}'")

        ar = comp.get("actionRef")
        if isinstance(ar, str) and ar not in action_ids:
            errs.append(f"{path}.actionRef: unknown action '{ar}'")

        ds = comp.get("dataSource") or comp.get("dataSourceRef")
        if isinstance(ds, str) and ds not in ds_ids:
            errs.append(f"{path}.dataSource: unknown '{ds}'")

        for j, b in enumerate(comp.get("buttons") or []):
            if isinstance(b, dict):
                bar = b.get("actionRef")
                if isinstance(bar, str) and bar not in action_ids:
                    errs.append(f"{path}.buttons[{j}].actionRef: unknown '{bar}'")

    return errs


# =============================================================================
# SOURCEKEY
# =============================================================================

def check_source_keys(pkg: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    key_names: Set[str] = set()
    ids: Set[str] = set()

    for ds in (pkg.get("dataSources") or []):
        if isinstance(ds, dict):
            if isinstance(ds.get("id"), str):
                ids.add(ds["id"])
            if isinstance(ds.get("keyName"), str):
                key_names.add(ds["keyName"])

    for path, comp in all_components(pkg):
        props = comp.get("props")
        if not isinstance(props, dict):
            continue
        sk = props.get("sourceKey")
        if not isinstance(sk, str) or not sk:
            continue
        if sk not in key_names:
            if sk in ids:
                errs.append(f"{path}.props.sourceKey '{sk}' matches id not keyName")
            else:
                errs.append(f"{path}.props.sourceKey '{sk}' not found")

    return errs


# =============================================================================
# BINDINGS
# =============================================================================

BIND_RE = re.compile(r"^data(\.[A-Za-z_][A-Za-z0-9_]*)+$")


def check_bindings(pkg: Dict[str, Any]) -> List[str]:
    errs: List[str] = []

    def walk(x: Any, p: str):
        if isinstance(x, dict):
            if "bind" in x and len(x) == 1 and isinstance(x.get("bind"), str):
                b = x["bind"]
                if not BIND_RE.match(b):
                    errs.append(f"{p}.bind: invalid '{b}'")
            for k, v in x.items():
                walk(v, f"{p}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{p}[{i}]")

    walk(pkg, "$")
    return errs


# =============================================================================
# DUPLICATES
# =============================================================================

def check_duplicates(pkg: Dict[str, Any]) -> List[str]:
    errs: List[str] = []

    def dupes(vals: List[str]) -> Set[str]:
        seen, dup = set(), set()
        for v in vals:
            (dup if v in seen else seen).add(v)
        return dup

    ds_ids = [d["id"] for d in (pkg.get("dataSources") or []) 
              if isinstance(d, dict) and isinstance(d.get("id"), str)]
    ds_keys = [d["keyName"] for d in (pkg.get("dataSources") or []) 
               if isinstance(d, dict) and isinstance(d.get("keyName"), str)]
    ac_ids = [a["id"] for a in (pkg.get("actions") or []) 
              if isinstance(a, dict) and isinstance(a.get("id"), str)]
    comp_ids = [c.get("id") for _, c in all_components(pkg) if isinstance(c.get("id"), str)]

    for d in dupes(ds_ids):
        errs.append(f"$.dataSources: duplicate id '{d}'")
    for d in dupes(ds_keys):
        errs.append(f"$.dataSources: duplicate keyName '{d}'")
    for d in dupes(ac_ids):
        errs.append(f"$.actions: duplicate id '{d}'")
    for d in dupes(comp_ids):
        errs.append(f"$.sections: duplicate id '{d}'")

    return errs


# =============================================================================
# SECURITY
# =============================================================================

UNSAFE = [
    (re.compile(r"<\s*script\b", re.I), "script tag"),
    (re.compile(r"javascript\s*:", re.I), "javascript:"),
    (re.compile(r"\bon\w+\s*=", re.I), "on*= handler"),
    (re.compile(r"<\s*iframe\b", re.I), "iframe"),
    (re.compile(r"<\s*object\b", re.I), "object"),
    (re.compile(r"<\s*embed\b", re.I), "embed"),
    (re.compile(r"<\s*meta\b[^>]*http-equiv", re.I), "meta http-equiv"),
    (re.compile(r"<\s*base\b", re.I), "base tag"),
    (re.compile(r"expression\s*\(", re.I), "css expression()"),
    (re.compile(r"url\s*\(\s*['\"]?\s*data:", re.I), "data: url()"),
    (re.compile(r"@import\s+", re.I), "css @import"),
]


def check_security(pkg: Dict[str, Any]) -> List[str]:
    hits: List[str] = []

    def scan(x: Any, path: str):
        if isinstance(x, str):
            for pat, name in UNSAFE:
                if pat.search(x):
                    hits.append(f"{path}: {name}")
        elif isinstance(x, dict):
            for k, v in x.items():
                scan(v, f"{path}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                scan(v, f"{path}[{i}]")

    scan(pkg, "$")
    return hits


# =============================================================================
# ALPINE
# =============================================================================

ALPINE_BAD = [
    (re.compile(r"x-html\s*=", re.I), "x-html"),
    (re.compile(r"x-on\s*:\s*\w+\s*=\s*['\"][^'\"]*\(", re.I), "x-on with call"),
    (re.compile(r"@\w+\s*=\s*['\"][^'\"]*\(", re.I), "@ with call"),
    (re.compile(r"x-init\s*=\s*['\"][^'\"]*eval\s*\(", re.I), "x-init eval"),
]


def check_alpine(pkg: Dict[str, Any]) -> List[str]:
    errs: List[str] = []

    def walk(x: Any, p: str):
        if isinstance(x, str):
            for pat, name in ALPINE_BAD:
                if pat.search(x):
                    errs.append(f"{p}: {name}")
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(v, f"{p}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{p}[{i}]")

    walk(pkg, "$")
    return errs


# =============================================================================
# TAILWIND
# =============================================================================

TW_ARBIT = re.compile(r"\[[^\]]+\]")
TW_BAD = [
    (re.compile(r"content-\[[^\]]*<", re.I), "content-[] HTML"),
    (re.compile(r"javascript:", re.I), "javascript:"),
    (re.compile(r"url\([^)]*\)", re.I), "url()"),
]


def check_tailwind(pkg: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for path, comp in all_components(pkg):
        cn = comp.get("className") or comp.get("class")
        if not isinstance(cn, str):
            continue
        if len(cn) > 500:
            out.append(f"{path}.className: too long ({len(cn)})")
        if TW_ARBIT.search(cn):
            out.append(f"{path}.className: arbitrary '[...]'")
        for pat, name in TW_BAD:
            if pat.search(cn):
                out.append(f"{path}.className: {name}")
    return out


# =============================================================================
# THEME
# =============================================================================

HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def check_theme(pkg: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    theme = pkg.get("theme")
    if not isinstance(theme, dict):
        return []
    c = theme.get("primaryColor")
    if isinstance(c, str) and not HEX_RE.match(c):
        errs.append("$.theme.primaryColor: must be hex")
    dm = theme.get("darkMode")
    if dm is not None and not isinstance(dm, bool):
        errs.append("$.theme.darkMode: must be boolean")
    return errs


# =============================================================================
# ROUTE
# =============================================================================

ROUTE_BAD = [
    (re.compile(r"<\s*script", re.I), "script"),
    (re.compile(r"javascript:", re.I), "javascript:"),
    (re.compile(r"\.\./"), "path traversal"),
    (re.compile(r"[<>\"']"), "special chars"),
]


def check_route(pkg: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    r = pkg.get("route") or meta_of(pkg).get("route")
    
    if isinstance(r, dict):
        for k, v in r.items():
            if isinstance(v, str):
                for pat, name in ROUTE_BAD:
                    if pat.search(v):
                        errs.append(f"$.route.{k}: {name}")
    elif isinstance(r, str):
        for pat, name in ROUTE_BAD:
            if pat.search(r):
                errs.append(f"$.route: {name}")
    return errs


# =============================================================================
# COMPLETENESS
# =============================================================================

def check_completeness(pkg: Dict[str, Any]) -> List[str]:
    _, secs = _sections_base_and_list(pkg)
    w: List[str] = []
    if not secs:
        w.append("$.sections: empty")
    for path, c in all_components(pkg):
        props = c.get("props")
        if isinstance(props, dict) and not props:
            w.append(f"{path}.props: empty")
    return w


# =============================================================================
# MAIN API
# =============================================================================

@dataclass
class ValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def __str__(self) -> str:
        if self.valid and not self.warnings:
            return "✅ Valid"
        lines = []
        if not self.valid:
            lines.append(f"❌ Invalid ({len(self.errors)} errors)")
            for e in self.errors[:10]:
                lines.append(f"  • {e}")
            if len(self.errors) > 10:
                lines.append(f"  ... +{len(self.errors)-10} more")
        if self.warnings:
            lines.append(f"⚠️  {len(self.warnings)} warnings")
            for w in self.warnings[:5]:
                lines.append(f"  • {w}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"valid": self.valid, "errors": self.errors, "warnings": self.warnings}


def validate(
    template: Dict[str, Any],
    allowed_endpoints: Optional[Set[str]] = None,
    schema_path: str = SCHEMA_PATH,
) -> ValidationResult:
    """
    Validate an EXPOZY template.
    
    Args:
        template: Template dict to validate
        allowed_endpoints: Optional allowlist of endpoints
        schema_path: Path to JSON Schema file
        
    Returns:
        ValidationResult with .valid, .errors, .warnings
    """
    errors: List[str] = []
    warnings: List[str] = []

    # Ajv schema validation
    errors.extend(ajv_check(template, schema_path))
    
    # Semantic checks
    errors.extend(check_endpoints(template, allowed_endpoints))
    errors.extend(check_refs(template))
    errors.extend(check_source_keys(template))
    errors.extend(check_bindings(template))
    errors.extend(check_duplicates(template))
    
    # Security checks
    errors.extend(check_security(template))
    errors.extend(check_alpine(template))
    errors.extend(check_theme(template))
    errors.extend(check_route(template))

    # Warnings
    warnings.extend(check_tailwind(template))
    warnings.extend(check_completeness(template))

    return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)


def validate_json(json_str: str, **kwargs) -> ValidationResult:
    """Validate from JSON string."""
    try:
        template = json.loads(json_str)
    except json.JSONDecodeError as e:
        return ValidationResult(valid=False, errors=[f"JSON parse error: {e}"])
    return validate(template, **kwargs)


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python validator.py <template.json>")
        print("       python validator.py '<json string>'")
        sys.exit(1)

    arg = sys.argv[1]
    
    if not arg.startswith("{"):
        try:
            with open(arg, "r", encoding="utf-8") as f:
                data = f.read()
        except FileNotFoundError:
            print(f"File not found: {arg}")
            sys.exit(1)
    else:
        data = arg

    result = validate_json(data)
    print(result)
    sys.exit(0 if result.valid else 1)