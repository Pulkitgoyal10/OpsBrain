"""Orchestrates graph extraction for one document: load -> extract -> Document
node + MENTIONS edges. Auto-detects table vs. prose extraction mode instead of
requiring the caller to know it in advance, so it works for an arbitrary
uploaded file - PDF or spreadsheet - not just the hardcoded sample docs.

Shared by scripts/run_graph_pipeline.py and the POST /upload endpoint.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from app.graph import schema
from app.graph.extractor import extract_from_prose, extract_from_table, has_maintenance_table
from app.graph.schema import GraphNode, GraphRelationship
from app.ingestion.base import DocumentLoader

# Entity labels a Document gets a MENTIONS edge to (not MaintenanceEvent, which
# is reached via PERFORMED_ON/BY, nor Location).
_MENTIONABLE = {schema.EQUIPMENT, schema.SUBSTANCE, schema.REGULATION, schema.PERSON}


def build_graph_from_document(
    loader: DocumentLoader,
    path: Path,
    *,
    doc_type: str,
    title: str,
) -> tuple[list[GraphNode], list[GraphRelationship]]:
    """Extract a Document node, its content nodes/relationships, and MENTIONS
    edges tying them together. Mode (deterministic table vs. LLM prose) is
    auto-detected per document: if any extracted table matches the
    maintenance inspection header shape, the whole document is treated as
    table mode (matching how our real report-shaped fixtures work); otherwise
    every chunk goes through the LLM prose path. This keeps the LLM out of the
    tabular case that broke in Phase 3.5 for any future report-shaped upload,
    not just the ones hardcoded by filename.
    """
    filename = path.name
    chunks = loader.load(path)
    tables = loader.extract_tables(path)

    nodes: list[GraphNode] = [
        GraphNode(
            schema.DOCUMENT,
            {
                "filename": filename,
                "doc_type": doc_type,
                "title": title,
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    ]
    rels: list[GraphRelationship] = []

    if has_maintenance_table(tables):
        chunk_ids_by_page: dict[int | None, list[str]] = defaultdict(list)
        for c in chunks:
            chunk_ids_by_page[c.metadata.page_number].append(c.metadata.chunk_id)
        full_text = "\n".join(c.text for c in chunks)
        content_nodes, content_rels = extract_from_table(
            tables,
            source_filename=filename,
            full_text=full_text,
            chunk_ids_by_page=dict(chunk_ids_by_page),
        )
    else:
        content_nodes, content_rels = [], []
        for chunk in chunks:
            n, r = extract_from_prose(chunk)
            content_nodes.extend(n)
            content_rels.extend(r)

    nodes.extend(content_nodes)
    rels.extend(content_rels)

    mentioned: set[tuple[str, str]] = {
        (n.label, n.key_value) for n in content_nodes if n.label in _MENTIONABLE
    }
    for label, key in mentioned:
        rels.append(GraphRelationship(schema.MENTIONS, schema.DOCUMENT, filename, label, key))

    # Tags every relationship (MENTIONS + whatever extract_from_table/
    # extract_from_prose built) with which document asserted it - needed so
    # DELETE /documents/{filename} can tell "this edge was only ever
    # asserted by the document being deleted" apart from "another surviving
    # document also asserts it", the same problem source_chunk_ids/sources
    # already solves for nodes. See neo4j_store.merge_relationship.
    for r in rels:
        r.properties["source_document"] = filename

    return nodes, rels
