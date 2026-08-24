"""Neo4j write layer for the knowledge graph (CLAUDE.md B3).

Mirrors the qdrant_store.py singleton pattern. MERGEs on natural keys so an
entity mentioned across many chunks/docs collapses to one node, accumulating
its source_chunk_ids for provenance (the shared key back to Qdrant).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import certifi
from dotenv import load_dotenv
from neo4j import Driver, GraphDatabase

from app.graph import schema
from app.graph.schema import GraphNode, GraphRelationship

# Matches the chunker's chunk_id format exactly: f"{stem}_p{page}_{index}"
# (app/ingestion/chunker.py) - just to pull the page segment back out. page
# is a digit string for a paginated source, or the literal "None" for a
# non-paginated one (chunker.py's f-string renders page_number=None as that
# literal text). Doesn't attempt to recover the filename from the stem -
# extensions vary by loader (.pdf, .xlsx, .csv, ...) and can't be inferred
# from the stem alone; _encode_source takes source_document directly instead.
_CHUNK_ID_RE = re.compile(r"_p(?P<page>\w+)_\d+$")

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

_driver: Driver | None = None


def get_driver() -> Driver:
    global _driver
    if _driver is None:
        uri = os.environ["NEO4J_URI"]
        user = os.environ["NEO4J_USER"]
        password = os.environ["NEO4J_PASSWORD"]
        if not (uri and user and password):
            raise RuntimeError(
                "NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD must be set in backend/.env"
            )
        # The python.org macOS build ships with an empty stdlib SSL trust store
        # (the "Install Certificates" step is never run), so the neo4j driver's
        # neo4j+s:// TLS can't verify AuraDB's cert chain. Point OpenSSL at
        # certifi's CA bundle so verification succeeds. httpx-based clients
        # (Cohere/Qdrant/Groq) already use certifi directly, which is why only
        # this driver was affected.
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        _driver = GraphDatabase.driver(uri, auth=(user, password))
    return _driver


def ensure_constraints(session) -> None:
    for statement in schema.constraint_statements():
        session.run(statement)


def clear_graph(session) -> None:
    """Delete all nodes/relationships. For a clean pipeline re-run."""
    session.run("MATCH (n) DETACH DELETE n")


def _encode_source(source_document: str, chunk_id: str) -> str:
    """One contributing-source record as a single string - Neo4j node
    properties can't store nested/mapped values, only primitives and arrays
    of one primitive type, so (filename, page, chunk_id) gets flattened to
    "filename|page|chunk_id" and split back apart on read
    (app.rag.graph_lookup). source_document is the real filename passed in
    directly (not reconstructed from chunk_id, which has no extension once
    Path.stem strips it - reconstructing it would require guessing the
    original format). page is "None" if chunk_id doesn't carry a page
    segment matching the chunker's format (e.g. malformed input).
    """
    match = _CHUNK_ID_RE.search(chunk_id)
    page = match.group("page") if match else "None"
    return f"{source_document}|{page}|{chunk_id}"


def merge_node(session, node: GraphNode) -> None:
    key_prop = schema.NATURAL_KEY[node.label]
    key_value = node.properties[key_prop]
    # source_chunk_ids is unioned (not overwritten) so provenance accumulates
    # across every chunk/doc the entity appears in; other props are just set.
    chunk_ids = node.properties.get("source_chunk_ids", [])

    # Label and key_prop come from our own schema constants (never user input),
    # so interpolating them into the query is safe; values stay parameterized.
    if node.label in schema.MULTI_SOURCE_LABELS:
        # source_document is unreliable for a node that can be MERGEd across
        # several documents: a Substance/Location/etc. mentioned in more than
        # one doc had this scalar silently overwritten by whichever write ran
        # last, corrupting any citation built from it (root cause of the bug
        # fixed here). Exclude it from the blind SET n += $props and instead
        # accumulate one `sources` entry per contributing chunk, unioned
        # across every merge call the same way source_chunk_ids already is -
        # every document that legitimately mentions this entity is tracked,
        # not just the most recent one.
        scalar_props = {
            k: v
            for k, v in node.properties.items()
            if k not in ("source_chunk_ids", "source_document")
        }
        source_document = node.properties.get("source_document", "")
        new_sources = [_encode_source(source_document, cid) for cid in chunk_ids]
        session.run(
            f"MERGE (n:{node.label} {{{key_prop}: $key_value}}) "
            f"SET n += $props "
            f"SET n.source_chunk_ids = "
            f"  [x IN coalesce(n.source_chunk_ids, []) WHERE NOT x IN $chunk_ids] + $chunk_ids "
            f"SET n.sources = "
            f"  [x IN coalesce(n.sources, []) WHERE NOT x IN $new_sources] + $new_sources",
            key_value=key_value,
            props=scalar_props,
            chunk_ids=chunk_ids,
            new_sources=new_sources,
        )
        return

    # Document/MaintenanceEvent: natural key is unique per write (the
    # document itself, or a specific equipment+inspection-date pair), never
    # re-merged with a conflicting source_document from a different
    # extraction pass - the plain scalar is safe here, unlike above.
    scalar_props = {k: v for k, v in node.properties.items() if k != "source_chunk_ids"}
    session.run(
        f"MERGE (n:{node.label} {{{key_prop}: $key_value}}) "
        f"SET n += $props "
        f"SET n.source_chunk_ids = "
        f"  [x IN coalesce(n.source_chunk_ids, []) WHERE NOT x IN $chunk_ids] + $chunk_ids",
        key_value=key_value,
        props=scalar_props,
        chunk_ids=chunk_ids,
    )


def merge_relationship(session, rel: GraphRelationship) -> None:
    start_key_prop = schema.NATURAL_KEY[rel.start_label]
    end_key_prop = schema.NATURAL_KEY[rel.end_label]
    # source_document is excluded from the blind SET += (same last-write-wins
    # bug already fixed for MULTI_SOURCE_LABELS nodes would otherwise apply
    # here too - a shared edge asserted by two documents would have its
    # scalar clobbered by whichever merge ran last) and instead accumulated
    # into r.sources, the same "which documents assert this" list nodes use.
    # Needed for DELETE /documents/{filename} to distinguish "only this
    # document ever asserted this edge" from "another document also does".
    scalar_props = {k: v for k, v in rel.properties.items() if k != "source_document"}
    source_document = rel.properties.get("source_document")
    session.run(
        f"MATCH (a:{rel.start_label} {{{start_key_prop}: $start_key}}) "
        f"MATCH (b:{rel.end_label} {{{end_key_prop}: $end_key}}) "
        f"MERGE (a)-[r:{rel.type}]->(b) "
        f"SET r += $props "
        f"SET r.sources = CASE WHEN $source_document IS NULL THEN coalesce(r.sources, []) "
        f"  ELSE [x IN coalesce(r.sources, []) WHERE x <> $source_document] + [$source_document] "
        f"  END",
        start_key=rel.start_key,
        end_key=rel.end_key,
        props=scalar_props,
        source_document=source_document,
    )


def write_graph(nodes: list[GraphNode], relationships: list[GraphRelationship]) -> None:
    """MERGE all nodes first (so relationship endpoints exist), then edges."""
    driver = get_driver()
    with driver.session() as session:
        for node in nodes:
            merge_node(session, node)
        for rel in relationships:
            merge_relationship(session, rel)


@dataclass(frozen=True)
class GraphDeletionSummary:
    document_found: bool
    # nodes_deleted counts every DETACH DELETEd node (MULTI_SOURCE_LABELS
    # nodes whose sources became empty, every MaintenanceEvent this document
    # owned, and the Document node itself if present) - relationships
    # removed as a byproduct of those DETACH DELETEs aren't separately
    # counted, only the explicit r.sources-driven relationship deletions
    # below are (that's the number worth surfacing: confirmation that
    # edge-level provenance cleanup actually ran).
    nodes_deleted: int
    nodes_updated: int
    relationships_deleted: int


def _delete_document_graph_tx(tx, filename: str) -> GraphDeletionSummary:
    prefix = f"{filename}|"
    nodes_deleted = 0
    nodes_updated = 0
    relationships_deleted = 0

    # 1. Relationships this document asserted (app.graph.pipeline tags every
    # relationship with source_document, merge_relationship accumulates it
    # into r.sources) - strip this filename; delete the edge only if that
    # was its last remaining source, otherwise keep it with the trimmed list.
    rel_records = tx.run(
        "MATCH ()-[r]->() WHERE $filename IN coalesce(r.sources, []) "
        "RETURN elementId(r) AS eid, r.sources AS sources",
        filename=filename,
    ).data()
    for rec in rel_records:
        remaining = [s for s in rec["sources"] if s != filename]
        if remaining:
            tx.run(
                "MATCH ()-[r]->() WHERE elementId(r) = $eid SET r.sources = $remaining",
                eid=rec["eid"],
                remaining=remaining,
            )
        else:
            tx.run("MATCH ()-[r]->() WHERE elementId(r) = $eid DELETE r", eid=rec["eid"])
            relationships_deleted += 1

    # 2. MULTI_SOURCE_LABELS nodes this document contributed to. Queried by
    # stored provenance (n.sources), not by traversing Document-[:MENTIONS]->
    # n - MENTIONS never targets Location even though Location is
    # multi-source, so a MENTIONS-based traversal would silently skip it.
    # Strip this document's `sources` entries; derive the source_chunk_ids
    # to drop from the chunk-id segment of those same entries (chunk ids
    # don't encode the filename on their own). Delete the node outright if
    # it has no sources left, otherwise keep it with the trimmed lists.
    node_records = tx.run(
        "MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) "
        "AND any(s IN coalesce(n.sources, []) WHERE s STARTS WITH $prefix) "
        "RETURN elementId(n) AS eid, n.sources AS sources, "
        "n.source_chunk_ids AS source_chunk_ids",
        labels=list(schema.MULTI_SOURCE_LABELS),
        prefix=prefix,
    ).data()
    for rec in node_records:
        sources = rec["sources"] or []
        removed_chunk_ids = {s.split("|", 2)[2] for s in sources if s.startswith(prefix)}
        remaining_sources = [s for s in sources if not s.startswith(prefix)]
        remaining_chunk_ids = [
            c for c in (rec["source_chunk_ids"] or []) if c not in removed_chunk_ids
        ]
        if remaining_sources:
            tx.run(
                "MATCH (n) WHERE elementId(n) = $eid "
                "SET n.sources = $sources, n.source_chunk_ids = $chunk_ids",
                eid=rec["eid"],
                sources=remaining_sources,
                chunk_ids=remaining_chunk_ids,
            )
            nodes_updated += 1
        else:
            tx.run("MATCH (n) WHERE elementId(n) = $eid DETACH DELETE n", eid=rec["eid"])
            nodes_deleted += 1

    # 3. MaintenanceEvent rows this document exclusively owns (single-source
    # label, per schema.MULTI_SOURCE_LABELS - always fully deleted, never
    # trimmed).
    maintenance_count = tx.run(
        "MATCH (m:MaintenanceEvent {source_document: $filename}) RETURN count(m) AS c",
        filename=filename,
    ).single()["c"]
    tx.run(
        "MATCH (m:MaintenanceEvent {source_document: $filename}) DETACH DELETE m",
        filename=filename,
    )
    nodes_deleted += maintenance_count

    # 4. The Document node itself (DETACH DELETE removes its own MENTIONS
    # edges as a byproduct).
    document_found = (
        tx.run(
            "MATCH (d:Document {filename: $filename}) RETURN count(d) AS c",
            filename=filename,
        ).single()["c"]
        > 0
    )
    tx.run("MATCH (d:Document {filename: $filename}) DETACH DELETE d", filename=filename)
    if document_found:
        nodes_deleted += 1

    return GraphDeletionSummary(
        document_found=document_found,
        nodes_deleted=nodes_deleted,
        nodes_updated=nodes_updated,
        relationships_deleted=relationships_deleted,
    )


def delete_document_graph(driver: Driver, filename: str) -> GraphDeletionSummary:
    """Surgically retract one document's contribution to the graph: shared
    (MULTI_SOURCE_LABELS) nodes/relationships it contributed to are trimmed,
    not deleted, unless this was their only source. Everything runs in one
    write transaction, so a concurrent read never observes a half-deleted
    graph and a mid-sequence failure leaves the graph untouched.
    """
    with driver.session() as session:
        return session.execute_write(_delete_document_graph_tx, filename)
