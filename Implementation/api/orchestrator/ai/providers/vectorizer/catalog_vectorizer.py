"""
EXPOZY Catalog Vectorizer 
─────────────────────────────
Combines component_catalog.json + page_types.json at INDEX TIME,
chunks semantically, embeds with DashScope text-embedding-v4,
and upserts into DashVector.

Source files stay SEPARATE in the repo. Combined version only
exists as chunks in the vector DB + local chunk_store.json.

Usage:
    # Full pipeline (CI/CD)
    python catalog_vectorizer.py --catalog component_catalog.json --pages page_types.json

    # Query only (testing)
    python catalog_vectorizer.py --query-only --query "meeting booking modal"

Environment variables:
    DASHSCOPE_API_KEY
    DASHVECTOR_API_KEY
    DASHVECTOR_ENDPOINT
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import dashscope
from dashscope import TextEmbedding
import dashvector

# ─── Config ─────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("catalog_vectorizer")

DASHSCOPE_MODEL = "text-embedding-v4"
EMBEDDING_DIM = 1024
COLLECTION_NAME = "expozy_catalog"
BATCH_SIZE = 10
UPSERT_BATCH = 50


class ChunkType(str, Enum):
    GLOBAL_CONFIG = "global_config"
    COMPONENT = "component"
    COMPONENT_SUB = "component_sub"
    PAGE_LAYOUT = "page_layout"
    PAGE_SECTION = "page_section"
    PAGE_ENDPOINT = "page_endpoint"
    PAGE_RUNTIME = "page_runtime"
    VALIDATION_RULE = "validation_rule"
    WORKFLOW = "workflow"
    BUSINESS_CONTEXT = "business_context"
    GLOBAL_TYPE = "global_type"


@dataclass
class Chunk:
    id: str
    text: str
    chunk_type: ChunkType
    metadata: dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# 1. COMBINE — handled by combine_catalog.py
# ═══════════════════════════════════════════════════════════════════════════
# The combine step merges component_catalog.json + page_types.json into one
# unified document with cross-references and inlined component specs.
# See combine_catalog.py for the logic.


# ═══════════════════════════════════════════════════════════════════════════
# 2. CHUNK
# ═══════════════════════════════════════════════════════════════════════════

def _id(prefix: str, key: str) -> str:
    return hashlib.sha256(f"{prefix}::{key}".encode()).hexdigest()[:24]


def _jc(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)[:6000]


def chunk_combined(combined: dict) -> list[Chunk]:
    chunks: list[Chunk] = []

    # ── Global config ──
    g = combined["global"]
    chunks.append(Chunk(
        id=_id("global", "config"),
        text=_fmt_global(g),
        chunk_type=ChunkType.GLOBAL_CONFIG,
        metadata={"source": "global"},
    ))

    # ── Components ──
    for cid, cdef in combined["components"].items():
        chunks.append(Chunk(
            id=_id("component", cid),
            text=_fmt_component(cid, cdef),
            chunk_type=ChunkType.COMPONENT,
            metadata={
                "component_id": cid,
                "category": cdef.get("category", ""),
            },
        ))
        for sid, sdef in cdef.get("sub_components", {}).items():
            chunks.append(Chunk(
                id=_id("component_sub", f"{cid}.{sid}"),
                text=_fmt_sub(cid, sid, sdef),
                chunk_type=ChunkType.COMPONENT_SUB,
                metadata={"component_id": cid, "sub_component_id": sid},
            ))

    # ── Page types ──
    for pid, pdef in combined["page_types"].items():
        chunks.append(Chunk(
            id=_id("page", pid),
            text=_fmt_page(pid, pdef),
            chunk_type=ChunkType.PAGE_LAYOUT,
            metadata={
                "page_id": pid,
                "route": pdef.get("route", ""),
                "always_include": pdef.get("always_include", False),
            },
        ))

        for sec in pdef.get("sections", []):
            order = sec.get("order", 0)
            chunks.append(Chunk(
                id=_id("page_section", f"{pid}.s{order}"),
                text=_fmt_section(pid, sec),
                chunk_type=ChunkType.PAGE_SECTION,
                metadata={"page_id": pid, "section_order": order},
            ))

        if pdef.get("endpoints_used"):
            chunks.append(Chunk(
                id=_id("page_ep", pid),
                text=_fmt_endpoints(pid, pdef),
                chunk_type=ChunkType.PAGE_ENDPOINT,
                metadata={"page_id": pid},
            ))

        has_rt = any(pdef.get(k) for k in (
            "component_interactions", "alpine_page_state",
            "auto_fetched_endpoints", "content_guidelines"))
        if has_rt:
            chunks.append(Chunk(
                id=_id("page_runtime", pid),
                text=_fmt_runtime(pid, pdef),
                chunk_type=ChunkType.PAGE_RUNTIME,
                metadata={"page_id": pid},
            ))

    # ── Global types ──
    for gtid, gtdef in combined.get("global_types", {}).items():
        chunks.append(Chunk(
            id=_id("global_type", gtid),
            text=_fmt_global_type(gtid, gtdef),
            chunk_type=ChunkType.GLOBAL_TYPE,
            metadata={
                 "global_type_id": gtid,
                 "page_id": gtid,                          
                 "component_id": gtdef.get("component", ""), 
                 "component": gtdef.get("component", ""),
             },
        ))

    # ── Workflow ──
    wf = combined.get("generation_workflow", {})
    if wf:
        chunks.append(Chunk(
            id=_id("workflow", "generation"),
            text=_fmt_workflow(wf),
            chunk_type=ChunkType.WORKFLOW,
            metadata={"source": "generation_workflow"},
        ))

    # ── Business context ──
    bc = combined.get("business_context", {})
    if bc:
        chunks.append(Chunk(
            id=_id("business", "context"),
            text=_fmt_biz_ctx(bc),
            chunk_type=ChunkType.BUSINESS_CONTEXT,
            metadata={"source": "business_context"},
        ))

    # ── Validation rules ──
    vr = combined.get("validation_rules", {})
    if vr:
        chunks.append(Chunk(
            id=_id("validation", "rules"),
            text=_fmt_validation(vr),
            chunk_type=ChunkType.VALIDATION_RULE,
            metadata={"source": "validation_rules"},
        ))

    log.info("Chunked: %d total", len(chunks))
    return chunks


# ─── Formatters ──────────────────────────────────────────────────────────

def _fmt_global(g: dict) -> str:
    lines = [
        "# EXPOZY Global Configuration",
        f"CSS: {g.get('css_framework')}, JS: {g.get('js_framework')}",
        "", "## Color Scheme", _jc(g.get("color_scheme", {})),
        "", "## Section Wrappers",
    ]
    for name, w in g.get("section_wrappers", {}).items():
        desc = w.get("description", "") if isinstance(w, dict) else ""
        lines.append(f"- {name}: {desc}")
        if isinstance(w, dict) and w.get("required_classes"):
            lines.append(f"  Classes: {w['required_classes']}")
    lines += ["", "## Global Components", str(g.get("global_components", {})),
              f"Image base: {g.get('image_base_url', '')}",
              f"Icons: {g.get('icon_library', '')} {g.get('icon_prefix', [])}"]
    return "\n".join(lines)


def _fmt_component(cid: str, c: dict) -> str:
    lines = [
        f"# Component: {c.get('name', cid)}",
        f"ID: {cid}",
        f"Category: {c.get('category', 'N/A')}",
        f"Max per page: {c.get('max_per_page', 'N/A')}",
        f"Description: {c.get('description', '')}",
        "", "## Properties", _jc(c.get("properties", {})),
    ]
    if c.get("endpoints"):
        lines += ["", "## Endpoints"]
        for ep in c["endpoints"]:
            lines.append(f"- {ep.get('ref', '?')} ({ep.get('method', '?')}) binding={ep.get('binding', '?')}")
            if ep.get("returns"):
                lines.append(f"  Returns: {_jc(ep['returns'])[:500]}")
    if c.get("alpine_actions"):
        lines += ["", "## Alpine Actions"]
        for a, d in c["alpine_actions"].items():
            lines.append(f"- {a}: {d}")
    if c.get("alpine_data"):
        lines += ["", "## Alpine Data", _jc(c["alpine_data"])]
    if c.get("structure"):
        lines += ["", "## Structure", _jc(c["structure"])]
    if c.get("section_wrapper_override"):
        lines.append(f"\nWrapper override: {c['section_wrapper_override']}")
    return "\n".join(lines)


def _fmt_sub(parent: str, sid: str, s: dict) -> str:
    return "\n".join([
        f"# Sub-component: {parent} → {sid}",
        f"Description: {s.get('description', '')}",
        "", "## Details", _jc(s),
    ])


def _fmt_page(pid: str, p: dict) -> str:
    lines = [
        f"# Page Type: {p.get('name', pid)}",
        f"ID: {pid}",
        f"Route: {p.get('route', 'N/A')}",
        f"Output: {p.get('output_file', 'N/A')}",
        f"Always include: {p.get('always_include', False)}",
        f"Description: {p.get('description', '')}",
    ]
    if p.get("include_condition"):
        lines.append(f"Condition: {p['include_condition']}")
    if p.get("_resolved_components"):
        lines.append(f"\nComponents: {', '.join(p['_resolved_components'])}")
    if p.get("endpoints_used"):
        lines.append(f"Endpoints: {', '.join(p['endpoints_used'])}")
    if p.get("ai_fills"):
        lines += ["", "## AI Fills", _jc(p["ai_fills"])]
    if p.get("content_placeholders"):
        lines += ["", "## Placeholders"]
        for ph, src in p["content_placeholders"].items():
            lines.append(f"  {ph} → {src}")
    if p.get("required_sections"):
        lines += ["", "## Required Sections"]
        for s in p["required_sections"]:
            lines.append(f"  - {s}")
    return "\n".join(lines)


def _fmt_section(pid: str, sec: dict) -> str:
    comps = []
    if sec.get("component"):
        comps.append(sec["component"])
    for col in ("left_column", "right_column"):
        for c in sec.get(col, {}).get("components", []):
            if c.get("component"):
                comps.append(c["component"])
    for k in ("sidebar", "content", "left", "right"):
        sub = sec.get(k, {})
        if isinstance(sub, dict) and sub.get("component"):
            comps.append(sub["component"])
    for ac in sec.get("after_columns", []):
        if ac.get("component"):
            comps.append(ac["component"])

    lines = [
        f"# Section: {pid} #{sec.get('order', '?')}",
        f"Components: {', '.join(comps) or 'custom'}",
        f"Wrapper: {sec.get('wrapper', 'default')}",
    ]
    if sec.get("wrapper_config"):
        lines.append(f"Config: {_jc(sec['wrapper_config'])}")
    if sec.get("layout"):
        lines.append(f"Layout: {sec['layout']}")
    if sec.get("section_id"):
        lines.append(f"Section ID: {sec['section_id']}")

    fills = {}
    if sec.get("ai_fills"):
        fills["section"] = sec["ai_fills"]
    for col in ("left_column", "right_column"):
        for c in sec.get(col, {}).get("components", []):
            if c.get("ai_fills"):
                fills[c.get("component", "?")] = c["ai_fills"]
    for k in ("left", "right"):
        sub = sec.get(k, {})
        if isinstance(sub, dict) and sub.get("ai_fills"):
            fills[sub.get("component", k)] = sub["ai_fills"]
    for ac in sec.get("after_columns", []):
        if ac.get("ai_fills"):
            fills[ac.get("component", "after")] = ac["ai_fills"]
    if fills:
        lines += ["", "## AI Fills", _jc(fills)]
    return "\n".join(lines)


def _fmt_endpoints(pid: str, p: dict) -> str:
    lines = [f"# Endpoints: {pid}", f"Route: {p.get('route', '')}", ""]
    for ep in p.get("endpoints_used", []):
        lines.append(f"  - {ep}")
    for pe in p.get("page_endpoints", []):
        lines.append(f"  - {pe.get('ref', '?')} binding={pe.get('binding', '?')} key={pe.get('key_name', '?')}")
    return "\n".join(lines)


def _fmt_runtime(pid: str, p: dict) -> str:
    lines = [f"# Runtime: {pid}"]
    ci = p.get("component_interactions", [])
    if ci:
        lines += ["", "## Interactions"]
        for ix in ci:
            lines.append(f"  {ix.get('source', '?')} → {ix.get('target', '?')} via {ix.get('trigger', '?')}: {ix.get('action', '')}")
    aps = p.get("alpine_page_state", {})
    if aps:
        lines += ["", "## Alpine State"]
        for k, v in aps.items():
            lines.append(f"  {k}: {v}")
    afe = p.get("auto_fetched_endpoints", [])
    if afe:
        lines += ["", "## Auto-fetch"]
        for ae in afe:
            lines.append(f"  - {ae.get('ref', '?')} as {ae.get('key_name', '?')} trigger={ae.get('trigger', '?')}")
    cg = p.get("content_guidelines", {})
    if cg:
        lines += ["", "## Content Guidelines"]
        if cg.get("description"):
            lines.append(f"  {cg['description']}")
        for n in cg.get("formatting_notes", []):
            lines.append(f"  - {n}")
    return "\n".join(lines)


def _fmt_global_type(gtid: str, gt: dict) -> str:
    lines = [
        f"# Global Type: {gt.get('name', gtid)}",
        f"ID: {gtid}", f"Output: {gt.get('output_file', '')}",
        f"Component: {gt.get('component', '')}",
        f"Phase: {gt.get('generation_phase', '')}",
        f"Description: {gt.get('description', '')}",
        "", "## AI Fills", _jc(gt.get("ai_fills", {})),
    ]
    if gt.get("dynamic_nav_generation"):
        lines += ["", "## Nav Generation", _jc(gt["dynamic_nav_generation"])]
    if gt.get("link_validation"):
        lines += ["", "## Link Validation", _jc(gt["link_validation"])]
    return "\n".join(lines)


def _fmt_workflow(wf: dict) -> str:
    lines = ["# Generation Workflow", wf.get("description", ""), "", "## Steps"]
    for s in wf.get("steps", []):
        lines.append(f"  {s['step']}. {s['name']} — {s['description']} → {s.get('output', 'N/A')}")
        if s.get("rules"):
            lines.append(f"     Rules: {_jc(s['rules'])[:400]}")
    lines += ["", "## Order", str(wf.get("generation_order", []))]
    return "\n".join(lines)


def _fmt_biz_ctx(bc: dict) -> str:
    lines = ["# Business Context Schema", bc.get("description", ""), "", "## Fields"]
    for fn, fd in bc.get("fields", {}).items():
        if isinstance(fd, dict):
            lines.append(f"  - {fn}: type={fd.get('type', '?')} required={fd.get('required', False)} example={fd.get('example', '')}")
        else:
            lines.append(f"  - {fn}: {fd}")
    return "\n".join(lines)


def _fmt_validation(vr: dict) -> str:
    lines = ["# Validation Rules", ""]
    for cat, rules in vr.items():
        if cat == "generation_checks":
            lines.append("## Generation Checks")
            for ch in rules:
                lines.append(f"  - [{ch.get('severity', '?')}] {ch.get('id', '?')}: {ch.get('description', '')}")
        elif isinstance(rules, dict):
            lines.append(f"## {cat}")
            for k, v in rules.items():
                lines.append(f"  - {k}: {v}")
        lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# 3. EMBED
# ═══════════════════════════════════════════════════════════════════════════

def embed_chunks(chunks: list[Chunk]) -> list[tuple[Chunk, list[float]]]:
    dashscope.api_key = os.environ["DASHSCOPE_API_KEY"]
    results = []
    texts = [c.text for c in chunks]
    for i in range(0, len(texts), BATCH_SIZE):
        bt = texts[i:i + BATCH_SIZE]
        bc = chunks[i:i + BATCH_SIZE]
        log.info("Embedding %d–%d / %d", i + 1, min(i + BATCH_SIZE, len(texts)), len(texts))
        resp = TextEmbedding.call(model=DASHSCOPE_MODEL, input=bt, dimension=EMBEDDING_DIM)
        if resp.status_code != 200:
            raise RuntimeError(f"DashScope error: {resp.status_code} — {resp.message}")
        for chunk, emb in zip(bc, resp.output["embeddings"]):
            results.append((chunk, emb["embedding"]))
        if i + BATCH_SIZE < len(texts):
            time.sleep(0.3)
    log.info("Embedded %d chunks", len(results))
    return results


# ═══════════════════════════════════════════════════════════════════════════
# 4. STORE
# ═══════════════════════════════════════════════════════════════════════════

# ─── Replace your init_collection function with this one ─────────────────

def init_collection(client: dashvector.Client, recreate: bool = False) -> dashvector.Collection:
    existing = client.list()
    if COLLECTION_NAME in existing:
        if recreate:
            log.info("Deleting collection '%s'", COLLECTION_NAME)
            client.delete(COLLECTION_NAME)
            # Wait for deletion to complete
            for i in range(30):
                time.sleep(2)
                current = client.list()
                if COLLECTION_NAME not in current:
                    log.info("Collection deleted after %ds", (i + 1) * 2)
                    break
            else:
                raise RuntimeError(f"Collection '{COLLECTION_NAME}' not deleted after 60s")
        else:
            return client.get(COLLECTION_NAME)

    log.info("Creating collection '%s'", COLLECTION_NAME)
    resp = client.create(
        name=COLLECTION_NAME,
        dimension=EMBEDDING_DIM,
        metric="cosine",
        fields_schema={
            "chunk_type": str,
            "component_id": str,
            "page_id": str,
            "category": str,
            "route": str,
            "source": str,
            "always_include": bool,
        },
    )
    if resp.code != 0:
        raise RuntimeError(f"Create collection failed: {resp.message}")

    # Poll until collection is SERVING
    for i in range(30):
        time.sleep(2)
        try:
            coll = client.get(COLLECTION_NAME)
            # Try a dummy stats call to verify it's ready
            stats = coll.stats()
            if stats.code == 0:
                log.info("Collection ready after %ds", (i + 1) * 2)
                return coll
        except Exception:
            pass
        log.info("Waiting for collection... (%ds)", (i + 1) * 2)

    raise RuntimeError(f"Collection '{COLLECTION_NAME}' not ready after 60s")

def upsert(collection: dashvector.Collection, embedded: list[tuple[Chunk, list[float]]]) -> None:
    docs = []
    for chunk, vec in embedded:
        docs.append(dashvector.Doc(id=chunk.id, vector=vec, fields={
            "chunk_type": chunk.chunk_type.value,
            "component_id": chunk.metadata.get("component_id", ""),
            "page_id": chunk.metadata.get("page_id", ""),
            "category": chunk.metadata.get("category", ""),
            "route": chunk.metadata.get("route", ""),
            "source": chunk.metadata.get("source", ""),
            "always_include": chunk.metadata.get("always_include", False),
            "text_preview": chunk.text[:500],
        }))
    for i in range(0, len(docs), UPSERT_BATCH):
        resp = collection.upsert(docs[i:i + UPSERT_BATCH])
        if resp.code != 0:
            raise RuntimeError(f"Upsert error: {resp.message}")
        log.info("Upserted %d–%d", i + 1, min(i + UPSERT_BATCH, len(docs)))
    log.info("Stored %d documents", len(docs))


# ═══════════════════════════════════════════════════════════════════════════
# 5. CHUNK STORE
# ═══════════════════════════════════════════════════════════════════════════

def save_chunk_store(chunks: list[Chunk], path: str = "chunk_store.json") -> None:
    store = {c.id: {"text": c.text, "chunk_type": c.chunk_type.value, "metadata": c.metadata} for c in chunks}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    log.info("Chunk store: %s (%d entries)", path, len(store))


# ═══════════════════════════════════════════════════════════════════════════
# 6. QUERY
# ═══════════════════════════════════════════════════════════════════════════

def query(collection: dashvector.Collection, text: str, top_k: int = 5,
          chunk_type: str | None = None, page_id: str | None = None) -> list[dict]:
    dashscope.api_key = os.environ["DASHSCOPE_API_KEY"]
    resp = TextEmbedding.call(model=DASHSCOPE_MODEL, input=[text], dimension=EMBEDDING_DIM)
    if resp.status_code != 200:
        raise RuntimeError(f"Embed error: {resp.message}")
    vec = resp.output["embeddings"][0]["embedding"]
    f = []
    if chunk_type: f.append(f"chunk_type = '{chunk_type}'")
    if page_id: f.append(f"page_id = '{page_id}'")
    r = collection.query(vector=vec, topk=top_k, filter=" AND ".join(f) if f else None,
                         output_fields=["chunk_type", "component_id", "page_id", "category", "route", "text_preview"])
    if r.code != 0:
        raise RuntimeError(f"Query error: {r.message}")
    return [{"id": d.id, "score": d.score, "chunk_type": d.fields.get("chunk_type", ""),
             "component_id": d.fields.get("component_id", ""), "page_id": d.fields.get("page_id", ""),
             "text_preview": d.fields.get("text_preview", "")} for d in r.output]


# ═══════════════════════════════════════════════════════════════════════════
# 7. PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

def run_pipeline(catalog_path: str, pages_path: str, recreate: bool = True) -> dict:
    log.info("═══ EXPOZY Catalog Vectorizer v2 ═══")

    # Step 1: Combine both source files into one
    # (uses combine_catalog.py logic — resolves component refs, inlines specs)
    from combine_catalog import combine
    combined = combine(catalog_path, pages_path)
    log.info("Combined: %d components, %d pages", len(combined["components"]), len(combined["page_types"]))

    with open("combined_catalog.json", "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

    chunks = chunk_combined(combined)
    save_chunk_store(chunks)

    dist = {}
    for c in chunks:
        dist[c.chunk_type.value] = dist.get(c.chunk_type.value, 0) + 1
    log.info("Distribution: %s", dist)

    embedded = embed_chunks(chunks)

    dv = dashvector.Client(api_key=os.environ["DASHVECTOR_API_KEY"], endpoint=os.environ["DASHVECTOR_ENDPOINT"])
    coll = init_collection(dv, recreate=recreate)
    upsert(coll, embedded)

    summary = {"total_chunks": len(chunks), "distribution": dist, "model": DASHSCOPE_MODEL, "collection": COLLECTION_NAME}
    log.info("═══ Complete ═══ %s", json.dumps(summary, indent=2))
    return summary


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="EXPOZY Catalog Vectorizer v2")
    p.add_argument("--catalog", default="component_catalog.json")
    p.add_argument("--pages", default="page_types.json")
    p.add_argument("--no-recreate", action="store_true")
    p.add_argument("--query", type=str, default=None)
    p.add_argument("--query-only", action="store_true")
    a = p.parse_args()

    if a.query_only and a.query:
        dv = dashvector.Client(api_key=os.environ["DASHVECTOR_API_KEY"], endpoint=os.environ["DASHVECTOR_ENDPOINT"])
        results = query(dv.get(COLLECTION_NAME), a.query, top_k=5)
        print(f"\n{'═' * 60}\nQuery: {a.query}\n{'═' * 60}")
        for r in results:
            print(f"\n[{r['score']:.4f}] {r['chunk_type']} | comp={r['component_id']} page={r['page_id']}")
            print(f"  {r['text_preview'][:200]}...")
    else:
        run_pipeline(a.catalog, a.pages, recreate=not a.no_recreate)
        if a.query:
            dv = dashvector.Client(api_key=os.environ["DASHVECTOR_API_KEY"], endpoint=os.environ["DASHVECTOR_ENDPOINT"])
            results = query(dv.get(COLLECTION_NAME), a.query, top_k=5)
            print(f"\n{'═' * 60}\nTest: {a.query}\n{'═' * 60}")
            for r in results:
                print(f"\n[{r['score']:.4f}] {r['chunk_type']} | comp={r['component_id']} page={r['page_id']}")
                print(f"  {r['text_preview'][:200]}...")