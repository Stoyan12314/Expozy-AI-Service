"""
EXPOZY Catalog Query Client (Runtime)
──────────────────────────────────────
It ONLY queries — never indexes. Indexing is handled by CI/CD.

Usage:
    from catalog_query import CatalogQuery

    query = CatalogQuery()
    results = query.search("meeting booking modal", top_k=5)
    context = query.get_generation_context("dental clinic homepage", page_id="homepage")
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import dashscope
from dashscope import TextEmbedding
import dashvector

log = logging.getLogger("catalog_query")

DASHSCOPE_MODEL = "text-embedding-v4"
EMBEDDING_DIM = 1024
COLLECTION_NAME = "expozy_catalog"


class CatalogQuery:
    """
    Stateless query client for the vectorized catalog.
    Connects to the existing DashVector collection (populated by CI/CD).
    """

    def __init__(
        self,
        dashscope_api_key: str | None = None,
        dashvector_api_key: str | None = None,
        dashvector_endpoint: str | None = None,
        collection_name: str = COLLECTION_NAME,
        chunk_store_path: str | None = None,
    ):
        dashscope.api_key = dashscope_api_key or os.environ["DASHSCOPE_API_KEY"]

        client = dashvector.Client(
            api_key=dashvector_api_key or os.environ["DASHVECTOR_API_KEY"],
            endpoint=dashvector_endpoint or os.environ["DASHVECTOR_ENDPOINT"],
        )
        self._collection = client.get(collection_name)

        # Optional: load chunk store for full-text retrieval
        self._chunk_store: dict | None = None
        if chunk_store_path and Path(chunk_store_path).exists():
            with open(chunk_store_path, "r", encoding="utf-8") as f:
                self._chunk_store = json.load(f)
            log.info("Loaded chunk store: %d entries", len(self._chunk_store))

    def _embed(self, text: str) -> list[float]:
        resp = TextEmbedding.call(
            model=DASHSCOPE_MODEL,
            input=[text],
            dimension=EMBEDDING_DIM,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Embedding failed: {resp.message}")
        return resp.output["embeddings"][0]["embedding"]

    def search(
        self,
        query: str,
        top_k: int = 5,
        chunk_type: str | None = None,
        component_id: str | None = None,
        page_id: str | None = None,
    ) -> list[dict]:
        """
        Semantic search over the catalog.

        Returns list of dicts with: id, score, chunk_type, component_id,
        page_id, text_preview, and optionally full_text (if chunk store loaded).
        """
        vector = self._embed(query)

        # Build filter
        filters = []
        if chunk_type:
            filters.append(f"chunk_type = '{chunk_type}'")
        if component_id:
            filters.append(f"component_id = '{component_id}'")
        if page_id:
            filters.append(f"page_id = '{page_id}'")

        resp = self._collection.query(
            vector=vector,
            topk=top_k,
            filter=" AND ".join(filters) if filters else None,
            output_fields=[
                "chunk_type", "component_id", "page_id",
                "category", "route", "text_preview",
            ],
        )

        if resp.code != 0:
            raise RuntimeError(f"Query error: {resp.message}")

        results = []
        for doc in resp.output:
            entry = {
                "id": doc.id,
                "score": doc.score,
                "chunk_type": doc.fields.get("chunk_type", ""),
                "component_id": doc.fields.get("component_id", ""),
                "page_id": doc.fields.get("page_id", ""),
                "category": doc.fields.get("category", ""),
                "route": doc.fields.get("route", ""),
                "text_preview": doc.fields.get("text_preview", ""),
            }
            # Attach full text if chunk store is available
            if self._chunk_store and doc.id in self._chunk_store:
                entry["full_text"] = self._chunk_store[doc.id]["text"]
            results.append(entry)

        return results

    def get_generation_context(
        self,
        prompt: str,
        page_id: str | None = None,
        top_k: int = 8,
    ) -> str:
        """
        Build a RAG context string for the AI page generator.

        Does a two-pass search:
          1. Page-specific results (if page_id given)
          2. General semantic results from the full catalog

        Returns a single string ready to inject into the generation prompt.
        """
        seen_ids: set[str] = set()
        all_results: list[dict] = []

        # Pass 1: page-specific
        if page_id:
            page_results = self.search(prompt, top_k=4, page_id=page_id)
            for r in page_results:
                if r["id"] not in seen_ids:
                    seen_ids.add(r["id"])
                    all_results.append(r)

        # Pass 2: general
        general_results = self.search(prompt, top_k=top_k)
        for r in general_results:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                all_results.append(r)

        # Build context from full text (or preview fallback)
        parts = []
        for r in all_results[:top_k]:
            text = r.get("full_text", r["text_preview"])
            parts.append(
                f"--- [{r['chunk_type']}] (score={r['score']:.3f}) ---\n{text}"
            )

        return "\n\n".join(parts)