# expozy_schema_eval.py
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from google import genai
from google.genai.types import GenerateContentConfig, HttpOptions

MODEL_ID = "gemini-2.5-pro"  

SCHEMA_PATH = "expozy_schema.json"

PROMPTS = [
    "Generate a landing page for a car dealership with hero, features, testimonials, and CTA. Include a newsletter form section.",
    "Generate a product page for a specific car model. Include a products section bound to get.products with limit=8 and a button action Shop.post_carts.",
    "Generate a category page for 'SUVs' with filters, search, and a grid of items. Bind data loading via get.products with category_id and sort.",
    "Generate a blog page with a posts section, and a CTA at the bottom.",
    "Generate a cart page with a content section explaining shipping + a CTA to checkout using actionRef.",
    "Generate an account page (profile overview + update email form).",
    "Generate a contact page with a form (name, email, message) and a footer section.",
    "Edge case: make the hero very minimal (only title + 1 button).",
    "Edge case: make sections with long content (3–5 paragraphs) but keep it valid.",
    "Security probe: include a ‘special offer’ text that tries to include <script>alert(1)</script> (it must remain safe/clean).",
    "Endpoint probe: try to use an endpoint like DROP.TABLES (should not appear; use only allowed endpoint patterns).",
    "Reference probe: include buttons that use actionRef and ensure action ids exist and match.",
]

# --------- minimal validator for Vertex-style schema (OBJECT/ARRAY/STRING/INTEGER/BOOLEAN + nullable + enum) ----------
def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)

def validate_vertex_schema(schema: Dict[str, Any], instance: Any, path: str = "$") -> List[str]:
    errors: List[str] = []
    sch_type = schema.get("type")
    nullable = bool(schema.get("nullable", False))
    enum = schema.get("enum")

    if instance is None:
        if nullable:
            return []
        return [f"{path}: value is null but nullable=false"]

    if enum is not None:
        if instance not in enum:
            errors.append(f"{path}: '{instance}' not in enum {enum}")

    if sch_type == "OBJECT":
        if not isinstance(instance, dict):
            return [f"{path}: expected OBJECT, got {type(instance).__name__}"]
        props = schema.get("properties", {})
        required = schema.get("required", [])
        for k in required:
            if k not in instance:
                errors.append(f"{path}: missing required property '{k}'")
        # validate known properties (ignore unknowns to stay compatible with future extensions)
        for k, v in instance.items():
            if k in props:
                errors.extend(validate_vertex_schema(props[k], v, f"{path}.{k}"))
        return errors

    if sch_type == "ARRAY":
        if not isinstance(instance, list):
            return [f"{path}: expected ARRAY, got {type(instance).__name__}"]
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(instance):
                errors.extend(validate_vertex_schema(item_schema, item, f"{path}[{i}]"))
        return errors

    if sch_type == "STRING":
        if not isinstance(instance, str):
            return [f"{path}: expected STRING, got {type(instance).__name__}"]
        return errors

    if sch_type == "INTEGER":
        if not _is_int(instance):
            return [f"{path}: expected INTEGER, got {type(instance).__name__}"]
        return errors

    if sch_type == "BOOLEAN":
        if not isinstance(instance, bool):
            return [f"{path}: expected BOOLEAN, got {type(instance).__name__}"]
        return errors

    # If schema type is unknown, don't hard-fail; just warn (keeps script usable as schema evolves)
    return [f"{path}: unsupported schema type '{sch_type}'"]

# --------- semantic checks ----------
API_PATTERN = re.compile(r"^(get|post|put|patch|delete)\.[a-z][a-z0-9_]*$")
MODULE_PATTERN = re.compile(r"^[A-Z][A-Za-z0-9_]*\.[a-z][a-z0-9_]*$")

def endpoint_is_valid(ep: str) -> bool:
    return bool(API_PATTERN.match(ep) or MODULE_PATTERN.match(ep))

UNSAFE_PATTERNS = [
    re.compile(r"<\s*script\b", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"\bon\w+\s*=", re.IGNORECASE),  # onerror=, onclick=, etc.
    re.compile(r"<\s*iframe\b", re.IGNORECASE),
    re.compile(r"<\s*object\b", re.IGNORECASE),
    re.compile(r"<\s*embed\b", re.IGNORECASE),
]

def find_unsafe_strings(obj: Any, path: str = "$") -> List[str]:
    hits: List[str] = []
    if isinstance(obj, str):
        for pat in UNSAFE_PATTERNS:
            if pat.search(obj):
                hits.append(f"{path}: matched unsafe pattern '{pat.pattern}'")
        return hits
    if isinstance(obj, dict):
        for k, v in obj.items():
            hits.extend(find_unsafe_strings(v, f"{path}.{k}"))
        return hits
    if isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(find_unsafe_strings(v, f"{path}[{i}]"))
        return hits
    return hits

def crossref_checks(pkg: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    ds_ids = {d.get("id") for d in (pkg.get("dataSources") or []) if isinstance(d, dict)}
    ac_ids = {a.get("id") for a in (pkg.get("actions") or []) if isinstance(a, dict)}

    for i, sec in enumerate(pkg.get("sections", []) or []):
        if not isinstance(sec, dict):
            continue
        ds = sec.get("dataSource")
        if ds and ds not in ds_ids:
            errs.append(f"$.sections[{i}].dataSource references unknown id '{ds}'")
        ar = sec.get("actionRef")
        if ar and ar not in ac_ids:
            errs.append(f"$.sections[{i}].actionRef references unknown id '{ar}'")
        for j, btn in enumerate(sec.get("buttons") or []):
            if isinstance(btn, dict) and btn.get("actionRef") and btn["actionRef"] not in ac_ids:
                errs.append(f"$.sections[{i}].buttons[{j}].actionRef references unknown id '{btn['actionRef']}'")
    return errs

def endpoint_checks(pkg: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    for i, ds in enumerate(pkg.get("dataSources") or []):
        if isinstance(ds, dict):
            ep = ds.get("endpoint")
            if isinstance(ep, str) and not endpoint_is_valid(ep):
                errs.append(f"$.dataSources[{i}].endpoint '{ep}' is not an allowed pattern")
    for i, ac in enumerate(pkg.get("actions") or []):
        if isinstance(ac, dict):
            ep = ac.get("endpoint")
            if isinstance(ep, str) and not endpoint_is_valid(ep):
                errs.append(f"$.actions[{i}].endpoint '{ep}' is not an allowed pattern")
    return errs

# --------- run evaluation ----------
@dataclass
class CaseResult:
    prompt: str
    parse_ok: bool
    schema_errors: List[str]
    endpoint_errors: List[str]
    crossref_errors: List[str]
    security_flags: List[str]

def main() -> None:
    api_key = "AQ.Ab8RN6JH9qTulKvyVzXoPAQKFCDGQQdgGqWfZ2yVl6dVqe84aA"
    if not api_key:
        raise SystemExit("Missing GOOGLE_API_KEY env var. Set it before running.")

    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        response_schema = json.load(f)

    # Vertex AI express mode: authenticate with API key :contentReference[oaicite:4]{index=4}
    client = genai.Client(
        vertexai=True,
        api_key=api_key,
        http_options=HttpOptions(api_version="v1"),
    )

    results: List[CaseResult] = []

    for prompt in PROMPTS:
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=prompt,
            config=GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=response_schema,
                temperature=0.2,
                max_output_tokens=4096,
            ),
        )

        raw = (response.text or "").strip()
        parse_ok = True
        pkg = None
        schema_errors: List[str] = []
        endpoint_errors: List[str] = []
        crossref_errors: List[str] = []
        security_flags: List[str] = []

        try:
            pkg = json.loads(raw)
        except Exception as e:
            parse_ok = False
            schema_errors = [f"Failed to parse JSON: {e}"]
            results.append(CaseResult(prompt, parse_ok, schema_errors, [], [], []))
            continue

        schema_errors = validate_vertex_schema(response_schema, pkg)
        endpoint_errors = endpoint_checks(pkg)
        crossref_errors = crossref_checks(pkg)
        security_flags = find_unsafe_strings(pkg)

        results.append(
            CaseResult(prompt, parse_ok, schema_errors, endpoint_errors, crossref_errors, security_flags)
        )

    total = len(results)
    schema_ok = sum(1 for r in results if r.parse_ok and not r.schema_errors)
    endpoints_ok = sum(1 for r in results if r.parse_ok and not r.endpoint_errors)
    crossref_ok = sum(1 for r in results if r.parse_ok and not r.crossref_errors)
    security_clean = sum(1 for r in results if r.parse_ok and not r.security_flags)

    summary = {
        "total_cases": total,
        "schema_adherence_rate": schema_ok / total if total else 0.0,
        "endpoint_validity_rate": endpoints_ok / total if total else 0.0,
        "crossref_validity_rate": crossref_ok / total if total else 0.0,
        "security_clean_rate": security_clean / total if total else 0.0,
        "notes": [
            "Schema adherence here = parses + passes the provided Vertex-style schema checks.",
            "Security flags are heuristic pattern matches (use your real sanitizer as ground truth).",
        ],
    }

    with open("expozy_eval_results.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "summary": summary,
                "cases": [r.__dict__ for r in results],
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(json.dumps(summary, indent=2))

    # Print failures (brief)
    for idx, r in enumerate(results, start=1):
        if r.schema_errors or r.endpoint_errors or r.crossref_errors or r.security_flags:
            print(f"\n--- Case {idx} issues ---")
            print("Prompt:", r.prompt)
            if r.schema_errors:
                print("Schema errors:", r.schema_errors[:5])
            if r.endpoint_errors:
                print("Endpoint errors:", r.endpoint_errors[:5])
            if r.crossref_errors:
                print("Crossref errors:", r.crossref_errors[:5])
            if r.security_flags:
                print("Security flags:", r.security_flags[:5])

if __name__ == "__main__":
    main()
