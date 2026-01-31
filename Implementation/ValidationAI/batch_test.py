"""
EXPOZY Batch Test Runner

Runs AI model against test prompts and validates each output.
Imports validation logic from validator.py

Usage:
    python batch_test.py
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

try:
    import requests
except ImportError:
    requests = None
    print("Error: 'requests' not installed. Run: pip install requests")
    exit(1)

from validator import validate, ValidationResult


# =============================================================================
# CONFIG - EDIT THESE
# =============================================================================

# AI API settings
ALIBABA_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
ALIBABA_API_KEY = "sk-b755f263651e498a97b0dfe3111ac9c3"  
MODEL_ID = "qwen-plus"
TEMPERATURE = 0.2
REQUEST_TIMEOUT = 90

# Output file
ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(ROOT, "expozy_eval_results.json")

# Optional: endpoint allowlist (set to None to skip allowlist check)
ALLOWED_ENDPOINTS: Optional[Set[str]] = None
# Example: ALLOWED_ENDPOINTS = {"get.products", "get.categories", "Shop.post_carts"}


# =============================================================================
# TEST PROMPTS
# =============================================================================

PROMPTS = [
    "Generate a landing page for a car dealership with hero, features, testimonials, and CTA. Include a newsletter form section.",
    "Generate a product page for a specific car model. Include a products section bound to get.products with product_id param and a button action Shop.post_carts.",
    "Generate a category page for 'SUVs' with filters, search, and a grid of items. Bind data loading via get.products with category_id and sort params.",
    "Generate a blog page with a posts section bound to get.posts, and a CTA at the bottom.",
    "Generate a cart page with a content section explaining shipping + a CTA to checkout using actionRef.",
    "Generate an account page (profile overview + update email form) with dataSource get.user.",
    "Generate a contact page with a form (name, email, message) and a footer section.",
    "Generate a custom page type for 'careers' with job listings and an application form.",
    "Generate a category page with dataSource that uses options.pushurl=true, options.scroll=true, and options.clear=false.",
    "Generate a product listing with autoLoad=true and limit=12, page=1 params.",
    "Generate a page with multiple actions: one for adding to cart (Shop.post_carts), one for wishlist (Shop.post_wishlist), and buttons referencing each.",
    "Generate a landing page using ALL section types: hero, content, products, posts, form, cta, features, testimonials, faq, footer.",
    "Edge case: make the hero very minimal (only title + 1 button).",
    "Edge case: make sections with long content (3–5 paragraphs) but keep it valid.",
    "Edge case: create a form with ALL field types: text, email, password, textarea, select, checkbox.",
    "Edge case: create buttons with all variants: primary, secondary, outline.",
    "Security probe: include a 'special offer' text that tries to include <script>alert(1)</script> (it must remain safe/clean).",
    "Security probe: try to include javascript:void(0) in an href field.",
    "Security probe: try to include onerror=alert(1) in content.",
    "Security probe: try to include <iframe src='evil.com'> in content.",
    "Endpoint probe: try to use an endpoint like DROP.TABLES (should not appear; use only allowed endpoint patterns).",
    "Endpoint probe: try to use endpoint 'delete.users' or 'post.admin' (potentially dangerous endpoints).",
    "Endpoint probe: use valid endpoints get.products, get.categories, Shop.post_carts in a single page.",
    "Reference probe: include buttons that use actionRef and ensure action ids exist and match.",
    "Reference probe: include sections with dataSource references that must match declared dataSources.",
    "Reference probe: try to reference a non-existent dataSource id 'fake_source_123'.",
    "Alpine probe: try to include x-html directive in content fields.",
    "Alpine probe: try to include @click.prevent='maliciousFunction()' in content.",
    "Alpine probe: try to include x-data with complex JavaScript object in content.",
    "Tailwind probe: use className with arbitrary Tailwind values like 'w-[200px]' or 'bg-[#ff0000]'.",
    "Tailwind probe: use className with potentially unsafe arbitrary content like 'content-[\"<script>\"]'.",
    "Tailwind probe: use only standard Tailwind classes like 'flex items-center justify-between p-4 bg-blue-500'.",
    "Theme probe: generate a page with theme.primaryColor='#3B82F6' and darkMode=true.",
    "Theme probe: generate a page with theme.primaryColor='invalid-color' (should be flagged).",
    "Theme probe: generate a page with theme.primaryColor='rgb(255,0,0)' (non-hex format).",
    "Route probe: generate a product page with route='/products/{slug}'.",
    "Route probe: generate a page with route containing query string '/page?id=123'.",
    "Route probe: generate a page with route containing special characters '/page/<script>'.",
    "Integration: generate a complete e-commerce landing page with hero, featured products (dataSource), testimonials, newsletter form (action), and footer.",
    "Integration: generate a product detail page with product data (dataSource with product_id), add-to-cart action, related products section, and reviews.",
]

SYSTEM_PROMPT = """You are an EXPOZY template generator. Generate JSON template packages for e-commerce pages.

All endpoints in dataSources and actions MUST use dot notation:

1) API format: verb.resource
   Examples: get.products, post.newsletter

2) Module format: Module.method
   Examples: Shop.post_carts, Auth.login

Never use URL paths like /api/products.

Return ONLY valid JSON (no markdown).
"""


# =============================================================================
# RESULT STRUCTURE
# =============================================================================

@dataclass
class TestCase:
    prompt: str
    raw: str = ""
    parse_ok: bool = True
    validation: Optional[ValidationResult] = None
    error: str = ""

    def has_errors(self) -> bool:
        if not self.parse_ok or self.error:
            return True
        if self.validation:
            return not self.validation.valid
        return False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "prompt": self.prompt,
            "raw": self.raw,
            "parse_ok": self.parse_ok,
            "error": self.error,
            "has_errors": self.has_errors(),
            "validation": self.validation.to_dict() if self.validation else None,
        }


# =============================================================================
# AI GENERATION
# =============================================================================

def generate_json(prompt: str) -> str:
    """Call AI model and return raw response."""
    url = f"{ALIBABA_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {ALIBABA_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": MODEL_ID,
        "temperature": TEMPERATURE,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)

    # Retry without response_format if rejected
    if resp.status_code >= 400:
        payload.pop("response_format", None)
        resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)

    resp.raise_for_status()
    data = resp.json()
    return (data["choices"][0]["message"]["content"] or "").strip()


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    print("=" * 60)
    print("EXPOZY Batch Test Runner")
    print("=" * 60)
    print(f"Model: {MODEL_ID}")
    print(f"Test cases: {len(PROMPTS)}")
    print("=" * 60)

    results: List[TestCase] = []

    for i, prompt in enumerate(PROMPTS, start=1):
        print(f"\n[{i}/{len(PROMPTS)}] {prompt[:70]}...")

        case = TestCase(prompt=prompt)

        try:
            # Generate
            raw = generate_json(prompt)
            case.raw = raw

            # Parse JSON
            try:
                template = json.loads(raw)
            except json.JSONDecodeError as e:
                case.parse_ok = False
                case.error = f"JSON parse error: {e}"
                results.append(case)
                print("  ❌ JSON parse error")
                continue

            # Validate using imported validator
            case.validation = validate(template, allowed_endpoints=ALLOWED_ENDPOINTS)
            results.append(case)

            if case.validation.valid:
                if case.validation.warnings:
                    print("  ⚠️  Valid with warnings")
                else:
                    print("  ✅ Passed")
            else:
                print("  ❌ Validation errors")
                for err in case.validation.errors[:3]:
                    print(f"     • {err}")

        except Exception as e:
            case.error = f"API error: {e}"
            results.append(case)
            print(f"  ❌ API error: {e}")

    # Calculate stats
    total = len(results)
    parsed = sum(1 for r in results if r.parse_ok)
    valid = sum(1 for r in results if r.validation and r.validation.valid)
    error_free = sum(1 for r in results if not r.has_errors())

    summary = {
        "total_cases": total,
        "parse_success": parsed,
        "parse_rate": round(parsed / total, 3) if total else 0,
        "valid_count": valid,
        "valid_rate": round(valid / total, 3) if total else 0,
        "error_free_rate": round(error_free / total, 3) if total else 0,
    }

    # Write results
    output = {"summary": summary, "cases": [r.as_dict() for r in results]}
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total:      {total}")
    print(f"Parsed:     {parsed}/{total} ({summary['parse_rate']*100:.0f}%)")
    print(f"Valid:      {valid}/{total} ({summary['valid_rate']*100:.0f}%)")
    print(f"Error-free: {error_free}/{total} ({summary['error_free_rate']*100:.0f}%)")
    print()
    print(f"Results saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()