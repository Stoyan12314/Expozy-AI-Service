"""
RAG Context Builder (FINAL)
──────────────────────────
Anchor + Expand pattern:

Step 1: semantic (business_context chunk)
Step 2: semantic (page_layout + workflow chunks)
Step 3: deterministic expansion using CatalogLoader as the source of truth:
  - CatalogLoader decides EXACT page structure + component IDs
  - DashVector is used only as a *keyed retrieval layer* (filters by page_id/component_id)
  - chunk_store.json provides authoritative metadata like section_order (no reliance on DV fields)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from api.orchestrator.ai.providers.vectorizer.catalog_query import CatalogQuery

try:
    from shared.utils.logging import get_logger
    logger = get_logger(__name__)
except ImportError:
    logger = logging.getLogger(__name__)


_DEFAULT_CHUNK_STORE = os.environ.get(
    "CHUNK_STORE_PATH",
    "/app/api/orchestrator/ai/data/chunk_store.json",
)

TOP_K_BUSINESS_CONTEXT = 2
TOP_K_PAGE_SELECTION = 40


class RAGContextBuilder:
    """
    Builds AI prompt context using hybrid retrieval:
      - Semantic search for discovery (Steps 1, 2)
      - Deterministic expansion for completeness (Step 3)
    """

    def __init__(
        self,
        query_client: Optional[CatalogQuery] = None,
        chunk_store_path: Optional[str] = None,
        catalog=None,
    ):
        self._chunk_store_path = chunk_store_path or _DEFAULT_CHUNK_STORE
        self._query = query_client or CatalogQuery(chunk_store_path=self._chunk_store_path)

        if catalog is not None:
            self._catalog = catalog
        else:
            from api.orchestrator.ai.providers.catalog_loader import get_catalog
            self._catalog = get_catalog()

        self._chunk_store: Dict[str, Dict[str, Any]] = {}
        self._load_chunk_store()

    async def _search(self, **kwargs) -> list:
        """Run a blocking CatalogQuery.search() in a thread pool."""
        return await asyncio.to_thread(partial(self._query.search, **kwargs))

    # ──────────────────────────────────────────────────────────────────
    # Step 1: Business context schema
    # ──────────────────────────────────────────────────────────────────

    async def business_context_schema_context(self) -> str:
        results = await self._search(
            query="business context schema fields company services location",
            top_k=TOP_K_BUSINESS_CONTEXT,
            chunk_type="business_context",
        )
        results = [self._enrich(r) for r in results]
        return self._assemble(results, header="BUSINESS CONTEXT SCHEMA")

    # ──────────────────────────────────────────────────────────────────
    # Step 2: Page selection
    # ──────────────────────────────────────────────────────────────────

    async def page_selection_context(self, business_context: Dict[str, Any]) -> str:
        seen: Set[str] = set()
        chunks: List[dict] = []

        page_layouts = await self._search(
            query="page types available for generation selection guidance priority",
            top_k=TOP_K_PAGE_SELECTION,
            chunk_type="page_layout",
        )
        chunks.extend(self._dedup([self._enrich(r) for r in page_layouts], seen))

        workflow = await self._search(
            query="generation workflow page selection rules order",
            top_k=2,
            chunk_type="workflow",
        )
        chunks.extend(self._dedup([self._enrich(r) for r in workflow], seen))

        return self._assemble(chunks, header="AVAILABLE PAGES AND WORKFLOW")

    # ──────────────────────────────────────────────────────────────────
    # Step 3: Page generation (deterministic expansion)
    # ──────────────────────────────────────────────────────────────────

    async def page_generation_context(
        self,
        page_type: str,
        business_context: Dict[str, Any],
        prompt: str,
    ) -> str:
        seen: Set[str] = set()
        sections: List[Tuple[str, List[dict]]] = []

        try:
            page_def = self._catalog.page_type(page_type)
        except KeyError:
            logger.warning("Unknown page_type '%s' in CatalogLoader; falling back to semantic-only", page_type)
            return await self._fallback_semantic(page_type, prompt)

        expected_section_count = len(page_def.get("sections", []))
        component_ids = set(self._catalog.component_ids_for_page(page_type))

        logger.info(
            "Step 3 deterministic expand | page=%s | expected_sections=%d | components=%s",
            page_type, expected_section_count, sorted(component_ids),
        )

        # 1) Page layout
        layout = await self._search(
            query=f"page {page_type} layout",
            top_k=1,
            chunk_type="page_layout",
            page_id=page_type,
        )
        layout_chunks = self._dedup([self._enrich(r) for r in layout], seen)

        # 2) Page sections
        sec_top_k = max(25, expected_section_count + 10)
        sec = await self._search(
            query=f"page {page_type} sections",
            top_k=sec_top_k,
            chunk_type="page_section",
            page_id=page_type,
        )
        section_chunks = self._dedup([self._enrich(r) for r in sec], seen)
        section_chunks.sort(key=self._section_order_key)

        if expected_section_count > 0 and len(section_chunks) < expected_section_count:
            logger.warning(
                "Missing page sections for '%s' | got=%d expected=%d | index may be stale",
                page_type, len(section_chunks), expected_section_count,
            )
            return await self._fallback_semantic(page_type, prompt)

        page_struct = layout_chunks + section_chunks
        if page_struct:
            sections.append(("PAGE STRUCTURE", page_struct))

        # 3) Runtime + endpoints
        runtime, endpoints = await asyncio.gather(
            self._search(
                query=f"page {page_type} runtime interactions alpine state",
                top_k=3,
                chunk_type="page_runtime",
                page_id=page_type,
            ),
            self._search(
                query=f"page {page_type} endpoints",
                top_k=3,
                chunk_type="page_endpoint",
                page_id=page_type,
            ),
        )
        rt_chunks = (
            self._dedup([self._enrich(r) for r in runtime], seen)
            + self._dedup([self._enrich(r) for r in endpoints], seen)
        )
        if rt_chunks:
            sections.append(("RUNTIME & ENDPOINTS", rt_chunks))

        # 4) Components — fetch all in parallel
        comp_chunks: List[dict] = []
        found_components: Set[str] = set()

        async def _fetch_component(cid: str):
            res = await self._search(
                query=f"component {cid}",
                top_k=1,
                chunk_type="component",
                component_id=cid,
            )
            fetched = self._dedup([self._enrich(r) for r in res], seen)
            if not fetched:
                logger.warning("Component chunk not found | page=%s comp=%s", page_type, cid)
                return cid, []

            try:
                comp_def = self._catalog.component(cid)
            except KeyError:
                comp_def = None

            sub_chunks = []
            if comp_def and isinstance(comp_def.get("sub_components"), dict) and comp_def["sub_components"]:
                sub_count = len(comp_def["sub_components"])
                sub = await self._search(
                    query=f"sub-components of {cid}",
                    top_k=max(10, sub_count + 5),
                    chunk_type="component_sub",
                    component_id=cid,
                )
                sub_chunks = self._dedup([self._enrich(r) for r in sub], seen)

            return cid, fetched + sub_chunks

        component_results = await asyncio.gather(*[_fetch_component(cid) for cid in sorted(component_ids)])

        for cid, chunks in component_results:
            if chunks:
                comp_chunks.extend(chunks)
                found_components.add(cid)

        missing_components = sorted(component_ids - found_components)
        if missing_components:
            logger.warning(
                "Missing component chunks for '%s' | missing=%s | index may be stale",
                page_type, missing_components,
            )
            return await self._fallback_semantic(page_type, prompt)

        if comp_chunks:
            sections.append(("COMPONENT DEFINITIONS", comp_chunks))

        # 5) Global config
        gconf = await self._search(
            query="global config section wrappers color scheme CSS framework",
            top_k=2,
            chunk_type="global_config",
        )
        g_chunks = self._dedup([self._enrich(r) for r in gconf], seen)
        if g_chunks:
            sections.append(("GLOBAL CONFIG", g_chunks))

        return self._assemble_sections(sections)

    # ──────────────────────────────────────────────────────────────────
    # Global types (header/footer)
    # ──────────────────────────────────────────────────────────────────

    async def global_type_context(self, global_type: str, selected_pages: List[str]) -> str:
        seen: Set[str] = set()
        sections: List[Tuple[str, List[dict]]] = []

        gt = await self._search(
            query=f"{global_type} global type generation",
            top_k=1,
            chunk_type="global_type",
            page_id=global_type,
        )
        gt_chunks = self._dedup([self._enrich(r) for r in gt], seen)
        if not gt_chunks:
            logger.warning("Global type chunk not found with page_id=%s; falling back semantic", global_type)
            return await self._fallback_semantic(global_type, "global header/footer")

        sections.append((f"{global_type.upper()} DEFINITION", gt_chunks))

        comp_name = f"site_{global_type}"
        comp, gconf = await asyncio.gather(
            self._search(
                query=f"component {comp_name}",
                top_k=1,
                chunk_type="component",
                component_id=comp_name,
            ),
            self._search(
                query="global config section wrappers",
                top_k=1,
                chunk_type="global_config",
            ),
        )
        comp_chunks = self._dedup([self._enrich(r) for r in comp], seen)

        try:
            comp_def = self._catalog.component(comp_name)
        except KeyError:
            comp_def = None

        if comp_def and isinstance(comp_def.get("sub_components"), dict) and comp_def["sub_components"]:
            sub = await self._search(
                query=f"sub-components of {comp_name}",
                top_k=10,
                chunk_type="component_sub",
                component_id=comp_name,
            )
            comp_chunks.extend(self._dedup([self._enrich(r) for r in sub], seen))

        if comp_chunks:
            sections.append(("COMPONENT SPEC", comp_chunks))

        g_chunks = self._dedup([self._enrich(r) for r in gconf], seen)
        if g_chunks:
            sections.append(("GLOBAL CONFIG", g_chunks))

        return self._assemble_sections(sections)

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────

    def _load_chunk_store(self) -> None:
        path = Path(self._chunk_store_path)
        if not path.exists():
            logger.warning("chunk_store.json not found at %s (sorting may be degraded)", path)
            self._chunk_store = {}
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self._chunk_store = json.load(f) or {}
        except Exception as e:
            logger.warning("Failed to load chunk_store.json (%s): %s", path, e)
            self._chunk_store = {}

    def _enrich(self, r: dict) -> dict:
        rid = r.get("id")
        if not rid or rid not in self._chunk_store:
            return r

        store = self._chunk_store[rid]
        md = store.get("metadata", {}) or {}

        r.setdefault("full_text", store.get("text", ""))
        r.setdefault("chunk_type", store.get("chunk_type", r.get("chunk_type", "")))
        r.setdefault("metadata", md)

        if "component_id" not in r and isinstance(md, dict) and md.get("component_id"):
            r["component_id"] = md.get("component_id")
        if "page_id" not in r and isinstance(md, dict) and md.get("page_id"):
            r["page_id"] = md.get("page_id")

        return r

    @staticmethod
    def _section_order_key(chunk: dict) -> int:
        md = chunk.get("metadata") or {}
        try:
            return int(md.get("section_order", 0))
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _dedup(results: List[dict], seen: Set[str]) -> List[dict]:
        out: List[dict] = []
        for r in results:
            rid = r.get("id")
            if not rid or rid in seen:
                continue
            seen.add(rid)
            out.append(r)
        return out

    def _assemble(self, chunks: List[dict], header: Optional[str] = None) -> str:
        parts = []
        if header:
            parts.append(f"=== {header} ===\n")
        for r in chunks:
            text = r.get("full_text") or r.get("text_preview", "")
            ctype = r.get("chunk_type", "")
            score = r.get("score", 0)
            parts.append(f"--- [{ctype}] (relevance: {score:.3f}) ---\n{text}")
        return "\n\n".join(parts)

    def _assemble_sections(self, sections: List[Tuple[str, List[dict]]]) -> str:
        parts: List[str] = []
        for header, chunks in sections:
            parts.append("\n" + "=" * 60)
            parts.append(f"=== {header} ===")
            parts.append("=" * 60 + "\n")
            for r in chunks:
                text = r.get("full_text") or r.get("text_preview", "")
                parts.append(f"--- [{r.get('chunk_type','')}] ---\n{text}\n")
        return "\n".join(parts)

    async def _fallback_semantic(self, page_type: str, prompt: str) -> str:
        results = await self._search(
            query=f"{page_type} page layout sections components {prompt[:120]}",
            top_k=25,
        )
        results = [self._enrich(r) for r in results]
        return self._assemble(results, header=f"FALLBACK CONTEXT FOR {page_type.upper()}")