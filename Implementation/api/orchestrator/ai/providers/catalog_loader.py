"""
CatalogLoader — loads component_catalog.json and page_types.json into memory.

Single source of truth for:
  - Component definitions (what exists)
  - Page type blueprints (what to generate)
  - Validation rules (what's allowed)
  - Prompt construction data (what to tell the LLM)

Usage:
    from api.orchestrator.ai.catalog_loader import get_catalog

    catalog = get_catalog()
    catalog.component("hero_section")        # → full component dict
    catalog.page_type("homepage")            # → page type blueprint
    catalog.allowed_endpoints()              # → set of endpoint refs
    catalog.prompt_components("homepage")    # → component defs needed for this page
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

try:
    from shared.utils.logging import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


# =============================================================================
# PATHS 
# =============================================================================

_DEFAULT_CATALOG_PATH = Path("/app/api/orchestrator/ai/providers/schemas/component_catalog.json")
_DEFAULT_PAGE_TYPES_PATH = Path("/app/api/orchestrator/ai/providers/schemas/page_types.json")


# =============================================================================
# CATALOG LOADER
# =============================================================================

class CatalogLoader:
    """Loads and indexes both JSON files for fast lookup."""

    def __init__(
        self,
        catalog_path: Optional[Path] = None,
        page_types_path: Optional[Path] = None,
    ):
        self._catalog_path = catalog_path or _DEFAULT_CATALOG_PATH
        self._page_types_path = page_types_path or _DEFAULT_PAGE_TYPES_PATH

        self._catalog: Dict[str, Any] = {}
        self._page_types: Dict[str, Any] = {}

        # Indexed caches (built on load)
        self._components: Dict[str, Dict[str, Any]] = {}
        self._pages: Dict[str, Dict[str, Any]] = {}
        self._global_types: Dict[str, Dict[str, Any]] = {}
        self._validation: Dict[str, Any] = {}
        self._global_config: Dict[str, Any] = {}
        self._business_context_schema: Dict[str, Any] = {}
        self._workflow: Dict[str, Any] = {}

        # Derived sets (built on load)
        self._all_component_ids: Set[str] = set()
        self._all_endpoint_refs: Set[str] = set()
        self._all_listener_refs: Set[str] = set()
        self._all_directive_refs: Set[str] = set()
        self._always_include_pages: List[str] = []

        self._loaded = False

    # ── Loading ──────────────────────────────────────────────────────────

    def load(self) -> "CatalogLoader":
        """Load both JSON files and build indexes. Call once at startup."""
        if self._loaded:
            return self

        self._load_catalog()
        self._load_page_types()
        self._build_indexes()
        self._loaded = True

        logger.info(
            f"Catalog loaded | components={len(self._components)} "
            f"page_types={len(self._pages)} "
            f"global_types={len(self._global_types)} "
            f"endpoints={len(self._all_endpoint_refs)} "
            f"catalog_version={self._catalog.get('catalog_version', '?')}"
        )
        return self

    def _load_catalog(self) -> None:
        if not self._catalog_path.exists():
            raise FileNotFoundError(f"Component catalog not found: {self._catalog_path}")
        with open(self._catalog_path, "r", encoding="utf-8") as f:
            self._catalog = json.load(f)

    def _load_page_types(self) -> None:
        if not self._page_types_path.exists():
            raise FileNotFoundError(f"Page types not found: {self._page_types_path}")
        with open(self._page_types_path, "r", encoding="utf-8") as f:
            self._page_types = json.load(f)

    def _build_indexes(self) -> None:
        # Components
        self._components = self._catalog.get("components", {})
        self._all_component_ids = set(self._components.keys())

        # Global config
        self._global_config = self._catalog.get("global", {})

        # Validation rules from catalog
        self._validation = self._catalog.get("validation_rules", {})
        alpine = self._validation.get("alpine_js", {})
        self._all_listener_refs = set(alpine.get("listeners_allowed", []))
        self._all_directive_refs = set(alpine.get("directives_allowed", []))

        # Collect all endpoint refs from components
        for comp_id, comp in self._components.items():
            for ep in comp.get("endpoints", []):
                if isinstance(ep, dict) and "ref" in ep:
                    self._all_endpoint_refs.add(ep["ref"])
                elif isinstance(ep, str):
                    self._all_endpoint_refs.add(ep)

        # Page types
        self._pages = self._page_types.get("page_types", {})
        self._global_types = self._page_types.get("global_types", {})
        self._workflow = self._page_types.get("generation_workflow", {})
        self._business_context_schema = self._page_types.get("business_context", {})

        # Always-include pages (legacy field, kept for backwards compat)
        rules = {}
        for step in self._workflow.get("steps", []):
            if step.get("name") == "select_pages":
                rules = step.get("rules", {})
                break
        self._always_include_pages = rules.get("always_include", [])

    # ── Component accessors ──────────────────────────────────────────────

    def component(self, component_id: str) -> Dict[str, Any]:
        """Get full component definition by ID."""
        if component_id not in self._components:
            raise KeyError(f"Unknown component: '{component_id}'")
        return self._components[component_id]

    def component_ids(self) -> Set[str]:
        return self._all_component_ids

    def components_for_page(self, page_type_id: str) -> List[Dict[str, Any]]:
        """Get all component definitions needed by a page type."""
        page = self.page_type(page_type_id)
        comp_ids = self._extract_component_ids_from_page(page)
        return [self._components[cid] for cid in comp_ids if cid in self._components]

    def component_ids_for_page(self, page_type_id: str) -> list[str]:
        """Public, stable API: return component IDs referenced by this page type."""
        page = self.page_type(page_type_id)
        return self._extract_component_ids_from_page(page)



    def _extract_component_ids_from_page(self, page: Dict[str, Any]) -> List[str]:
        """Walk page type sections and collect all referenced component IDs."""
        ids: List[str] = []
        for section in page.get("sections", []):
            # Direct component reference
            if "component" in section:
                ids.append(section["component"])

            # Component group with columns
            if "left_column" in section:
                for comp in section["left_column"].get("components", []):
                    if "component" in comp:
                        ids.append(comp["component"])
            if "right_column" in section:
                for comp in section["right_column"].get("components", []):
                    if "component" in comp:
                        ids.append(comp["component"])
            if "after_columns" in section:
                for comp in section["after_columns"]:
                    if "component" in comp:
                        ids.append(comp["component"])

            # Sidebar/content layout
            if "sidebar" in section and isinstance(section["sidebar"], dict):
                if "component" in section["sidebar"]:
                    ids.append(section["sidebar"]["component"])
            if "content" in section and isinstance(section["content"], dict):
                if "component" in section["content"]:
                    ids.append(section["content"]["component"])

            # Two-column grid
            if "left" in section and isinstance(section["left"], dict):
                if "component" in section["left"]:
                    ids.append(section["left"]["component"])
            if "right" in section and isinstance(section["right"], dict):
                if "component" in section["right"]:
                    ids.append(section["right"]["component"])

        return ids

    # ── Page type accessors ──────────────────────────────────────────────

    def page_type(self, page_type_id: str) -> Dict[str, Any]:
        """Get page type blueprint."""
        if page_type_id in self._pages:
            return self._pages[page_type_id]
        if page_type_id in self._global_types:
            return self._global_types[page_type_id]
        raise KeyError(f"Unknown page type: '{page_type_id}'")

    def all_page_type_ids(self) -> List[str]:
        """All page type IDs (pages + globals)."""
        return list(self._pages.keys()) + list(self._global_types.keys())

    def content_page_type_ids(self) -> List[str]:
        """Page types only (no globals)."""
        return list(self._pages.keys())

    def global_type_ids(self) -> List[str]:
        """Global types only (header, footer)."""
        return list(self._global_types.keys())

    def always_include_pages(self) -> List[str]:
        return list(self._always_include_pages)

    def is_global_type(self, page_type_id: str) -> bool:
        return page_type_id in self._global_types

    def output_file(self, page_type_id: str) -> str:
        pt = self.page_type(page_type_id)
        return pt.get("output_file", f"{page_type_id}.html")

    def route(self, page_type_id: str) -> str:
        pt = self.page_type(page_type_id)
        return pt.get("route", "/")

    # ── Generic page metadata (drives worker_service logic) ──────────────

    def required_page_ids(self) -> List[str]:
        """
        Pages marked "required": true in page_types.json.
        These are always included regardless of AI selection.
        Reads from both page_types and global_types.
        """
        required = []
        for pid, cfg in self._pages.items():
            if cfg.get("required", False):
                required.append(pid)
        for gid, cfg in self._global_types.items():
            if cfg.get("required", False):
                required.append(gid)
        return required

    def requires(self, page_id: str) -> List[str]:
        """
        Pages that must be co-selected with this one.
        Reads "requires": ["other_page"] from page_types.json.
        Returns empty list if no pairings defined.
        """
        try:
            pt = self.page_type(page_id)
        except KeyError:
            return []
        return pt.get("requires", [])

    # ── Validation accessors ─────────────────────────────────────────────

    def allowed_endpoints(self) -> Set[str]:
        return set(self._all_endpoint_refs)

    def allowed_listeners(self) -> Set[str]:
        return set(self._all_listener_refs)

    def allowed_directives(self) -> Set[str]:
        return set(self._all_directive_refs)

    def endpoints_for_page(self, page_type_id: str) -> List[str]:
        """Get endpoint refs that a page type is allowed to use."""
        pt = self.page_type(page_type_id)
        return pt.get("endpoints_used", [])

    def section_wrappers(self) -> Dict[str, Any]:
        return self._global_config.get("section_wrappers", {})

    def required_components_for_page(self, page_type_id: str) -> List[str]:
        """Get component IDs marked required:true for a page type."""
        pt = self.page_type(page_type_id)
        required = []
        for section in pt.get("sections", []):
            if section.get("required", False) and "component" in section:
                required.append(section["component"])
        return required

    def required_sections_for_page(self, page_type_id: str) -> List[str]:
        """For legal pages, get the required content sections."""
        pt = self.page_type(page_type_id)
        return pt.get("required_sections", [])

    def error_hints(self) -> Dict[str, str]:
        """
        Return error_hints map from validator_hints in component_catalog.json.

        Keys: error code prefixes (e.g. "SEC-001", "HTM-001")
        Values: plain-English fix instructions for the AI retry feedback loop

        Used by worker.py _build_error_hints() to give the AI model
        actionable fix instructions on validation failure.

        Source chain:
          component_catalog.json → validator_hints.error_hints
          → CatalogLoader        → error_hints() returns the dict
          → worker.py            → _build_error_hints(errors, hints_map)

        NOTE: CatalogLoader loads component_catalog.json (the SOURCE file)
        into self._catalog, NOT combined_catalog.json. So we read from
        validator_hints directly — not from _validator (which only exists
        in the combined output).
        """
        hints = self._catalog.get("validator_hints", {})
        return hints.get("error_hints", {})
    def page_selection_rules(self) -> List[str]:
        """
        Return page selection guidance rules from component_catalog.json.

        Used by worker Step 2 (select_pages) to build the system prompt.
        Edit page_selection_rules in component_catalog.json to change
        selection behavior — no Python changes needed.
        """
        return self._catalog.get("page_selection_rules", [])
    # ── Prompt construction accessors ────────────────────────────────────

    def shared_rules_prompt(self) -> str:
        """
        Build shared system prompt from ai_generation_rules in catalog JSON.
        Fully generic — any new section added to the JSON is auto-included.
        No Python changes needed when rules are added/modified in the JSON.

        Handles three value types automatically:
          - list of strings  → bullet points (styling_rules, content_rules, etc.)
          - dict with "rule" children → anti-pattern block with WRONG/CORRECT examples
          - dict without "rule" children → flat key-value pairs (output_format, etc.)
          - plain string → rendered as-is

        Returns a formatted multi-line string ready for use as system prompt.
        """
        rules = self._catalog.get("ai_generation_rules", {})
        if not rules:
            logger.warning("No ai_generation_rules found in catalog — returning empty prompt")
            return ""

        parts = [rules.get("system_role", "")]

        for key, value in rules.items():
            if key == "system_role":
                continue

            heading = key.upper().replace("_", " ")

            # --- Simple list of strings (styling_rules, content_rules, etc.) ---
            if isinstance(value, list):
                parts.append(f"\n{heading}:")
                for item in value:
                    parts.append(f"- {item}")

            # --- Dict section (output_format, common_mistakes, etc.) ---
            elif isinstance(value, dict):
                desc = value.get("description", "")
                has_rule_children = any(
                    isinstance(v, dict) and "rule" in v
                    for v in value.values()
                )

                if has_rule_children:
                    # Anti-pattern rules with WRONG/CORRECT examples
                    parts.append(f"\n=== {heading} ===")
                    if desc:
                        parts.append(desc)
                    for rk, rv in value.items():
                        if rk == "description":
                            continue
                        if isinstance(rv, dict) and "rule" in rv:
                            parts.append(f"\n[{rk}] {rv['rule']}")
                            # WRONG example
                            if rv.get("wrong"):
                                parts.append(f"  WRONG: {rv['wrong']}")
                            elif rv.get("wrong_examples"):
                                parts.append(f"  WRONG: {rv['wrong_examples'][0]}")
                            # CORRECT example
                            if rv.get("correct"):
                                parts.append(f"  CORRECT: {rv['correct']}")
                            elif rv.get("correct_examples"):
                                parts.append(f"  CORRECT: {rv['correct_examples'][0]}")
                            elif rv.get("correct_standard"):
                                parts.append(f"  CORRECT: {rv['correct_standard']}")
                else:
                    # Flat dict (output_format, etc.)
                    parts.append(f"\n{heading}:")
                    if desc:
                        parts.append(f"- {desc}")
                    for dk, dv in value.items():
                        if dk == "description":
                            continue
                        if isinstance(dv, list):
                            for item in dv:
                                parts.append(f"- {item}")
                        elif isinstance(dv, str):
                            parts.append(f"- {dk}: {dv}")

            # --- Plain string ---
            elif isinstance(value, str):
                parts.append(f"\n{heading}:\n{value}")

        return "\n".join(parts)

    def business_context_schema(self) -> Dict[str, Any]:
        return self._business_context_schema

    def generation_workflow(self) -> Dict[str, Any]:
        return self._workflow

    def generation_order(self) -> List[str]:
        return self._workflow.get("generation_order", [])

    def ai_fills_for_page(self, page_type_id: str) -> Dict[str, Any]:
        """Get all ai_fills instructions for a page type, keyed by component."""
        pt = self.page_type(page_type_id)
        fills: Dict[str, Any] = {}

        # Top-level ai_fills
        if "ai_fills" in pt:
            fills["_page_level"] = pt["ai_fills"]

        # Per-section ai_fills
        for section in pt.get("sections", []):
            comp = section.get("component", section.get("component_group", "unknown"))
            if "ai_fills" in section:
                fills[comp] = section["ai_fills"]

            # Nested in columns
            for col_key in ("left_column", "right_column", "left", "right"):
                col = section.get(col_key)
                if isinstance(col, dict):
                    for sub in col.get("components", []):
                        if "ai_fills" in sub:
                            fills[sub.get("component", "unknown")] = sub["ai_fills"]
                    if "ai_fills" in col:
                        fills[col.get("component", "unknown")] = col["ai_fills"]

        return fills

    def content_placeholders_for_page(self, page_type_id: str) -> Dict[str, str]:
        """Get placeholder→business_context mapping for legal pages."""
        pt = self.page_type(page_type_id)
        return pt.get("content_placeholders", {})

    def global_config(self) -> Dict[str, Any]:
        return self._global_config

    # ── JSON Schema builders for AI response_format ──────────────────────

    def business_context_response_schema(self) -> Dict[str, Any]:
        """
        Build a JSON Schema for Qwen response_format from business_context.fields
        in page_types.json. This ensures the AI returns guaranteed valid JSON
        matching the catalog's business_context structure.

        Used by worker Step 1 (extract_business_context) via PageConfig.response_schema.
        """
        fields = self._business_context_schema.get("fields", {})

        properties: Dict[str, Any] = {}
        required: List[str] = []

        for name, defn in fields.items():
            if not isinstance(defn, dict):
                continue

            ftype = defn.get("type")

            if ftype == "string":
                properties[name] = {"type": "string"}
            elif ftype == "array_of_strings":
                properties[name] = {"type": "array", "items": {"type": "string"}}
            elif ftype == "array":
                # Check if it's a structured array (like languages with code+label)
                default = defn.get("default", [])
                if default and isinstance(default[0], dict) and "code" in default[0]:
                    properties[name] = {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string"},
                                "label": {"type": "string"},
                            },
                            "required": ["code", "label"],
                            "additionalProperties": False,
                        },
                    }
                else:
                    properties[name] = {"type": "array", "items": {"type": "string"}}
            elif ftype is None:
                # Nested object without explicit type (location, contact)
                nested_props: Dict[str, Any] = {}
                nested_req: List[str] = []
                for sub_name, sub_def in defn.items():
                    if isinstance(sub_def, dict) and sub_def.get("type"):
                        nested_props[sub_name] = {"type": "string"}
                        nested_req.append(sub_name)
                if nested_props:
                    properties[name] = {
                        "type": "object",
                        "properties": nested_props,
                        "required": nested_req,
                        "additionalProperties": False,
                    }
            else:
                properties[name] = {"type": "string"}

            if defn.get("required", False):
                required.append(name)

        all_keys = list(properties.keys())

        return {
            "type": "json_schema",
            "json_schema": {
                "name": "business_context",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": properties,
                    "required": all_keys,
                    "additionalProperties": False,
                },
            },
        }

    def page_selection_response_schema(self) -> Dict[str, Any]:
        """
        Build a JSON Schema for Qwen response_format for page selection.
        Uses actual page type IDs from catalog as enum values, ensuring
        the AI can only select valid page types.

        Only includes content pages — globals (header/footer) are added
        automatically by the worker.

        Used by worker Step 2 (select_pages) via PageConfig.response_schema.
        """
        page_ids = sorted(self.content_page_type_ids())

        return {
            "type": "json_schema",
            "json_schema": {
                "name": "page_selection",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "pages": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": page_ids,
                            },
                        },
                    },
                    "required": ["pages"],
                    "additionalProperties": False,
                },
            },
        }

    # ── Raw access ───────────────────────────────────────────────────────

    def raw_catalog(self) -> Dict[str, Any]:
        return self._catalog

    def raw_page_types(self) -> Dict[str, Any]:
        return self._page_types


# =============================================================================
# SINGLETON
# =============================================================================

_instance: Optional[CatalogLoader] = None


def get_catalog(
    catalog_path: Optional[Path] = None,
    page_types_path: Optional[Path] = None,
    force_reload: bool = False,
) -> CatalogLoader:
    """Get or create the singleton CatalogLoader."""
    global _instance
    if _instance is None or force_reload:
        _instance = CatalogLoader(catalog_path, page_types_path).load()
    return _instance


def reset_catalog() -> None:
    """For testing."""
    global _instance
    _instance = None