"""Load sample docs -> extract entities/relations -> MERGE into Neo4j.

Hybrid extraction (CLAUDE.md B3), mode auto-detected per document by
app.graph.pipeline.build_graph_from_document():
- maintenance report, spreadsheet inspection log -> deterministic table extraction
- SDS + work order                               -> LLM prose extraction

Requires NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD in backend/.env.
Usage: .venv/bin/python scripts/run_graph_pipeline.py [--clear]
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.graph.neo4j_store import clear_graph, ensure_constraints, get_driver, write_graph  # noqa: E402
from app.graph.pipeline import build_graph_from_document  # noqa: E402
from app.graph.schema import GraphNode, GraphRelationship  # noqa: E402
from app.ingestion.loader_registry import get_loader_for_file  # noqa: E402

SAMPLE_DOCS = BACKEND_DIR / "sample_docs"

DOCS = [
    {
        "file": "Sample Maintenance Inspection Report.pdf",
        "doc_type": "maintenance_report",
        "title": "Maintenance Inspection Report - Riverside Unit 3",
    },
    {
        "file": "Sample SDS Handout.pdf",
        "doc_type": "sds",
        "title": "Safety Data Sheet - Sodium Hypochlorite",
    },
    {
        "file": "Sample Work Order.pdf",
        "doc_type": "work_order",
        "title": "Work Order WO-2026-0142 - Pump P-102",
    },
    {
        "file": "Sample Spreadsheet Inspection Log.xlsx",
        "doc_type": "maintenance_report",
        "title": "Spreadsheet Inspection Log - Riverside Unit 3",
    },
]


def main() -> None:
    clear = "--clear" in sys.argv

    all_nodes: list[GraphNode] = []
    all_rels: list[GraphRelationship] = []

    for doc in DOCS:
        path = SAMPLE_DOCS / doc["file"]
        print(f"Extracting {doc['file']} ...")

        nodes, rels = build_graph_from_document(
            get_loader_for_file(path), path, doc_type=doc["doc_type"], title=doc["title"]
        )
        all_nodes.extend(nodes)
        all_rels.extend(rels)
        print(f"  {len(nodes)} nodes, {len(rels)} relationships extracted")

    driver = get_driver()
    with driver.session() as session:
        ensure_constraints(session)
        if clear:
            print("Clearing existing graph (--clear) ...")
            clear_graph(session)

    print(f"\nWriting {len(all_nodes)} nodes + {len(all_rels)} relationships to Neo4j ...")
    write_graph(all_nodes, all_rels)

    # Report actual persisted counts (post-dedup), not pre-MERGE totals.
    with driver.session() as session:
        node_rows = session.run(
            "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS c ORDER BY label"
        ).data()
        rel_rows = session.run(
            "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY t"
        ).data()

    print("\n--- Persisted graph (after dedup) ---")
    print("Nodes:")
    for row in node_rows:
        print(f"  {row['label']}: {row['c']}")
    print("Relationships:")
    for row in rel_rows:
        print(f"  {row['t']}: {row['c']}")
    print(f"\nTotal nodes: {sum(r['c'] for r in node_rows)}")
    print(f"Total relationships: {sum(r['c'] for r in rel_rows)}")


if __name__ == "__main__":
    main()
