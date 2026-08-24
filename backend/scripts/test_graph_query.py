"""Prove the graph closes the Phase 3 retrieval gap with deterministic queries.

Phase 3: "When is P-102's next inspection?" -> vector search ranked a prose
mention above the data row. Here the same question is a direct graph lookup.

Requires a populated Neo4j (run scripts/run_graph_pipeline.py first).
Usage: .venv/bin/python scripts/test_graph_query.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.graph.neo4j_store import get_driver  # noqa: E402

# The Phase 3 question, as a direct traversal - no ranking, no LLM.
P102_NEXT_DUE = """
MATCH (e:Equipment {tag: 'P-102'})<-[:PERFORMED_ON]-(m:MaintenanceEvent)
RETURN m.next_due_date  AS next_due,
       m.inspection_date AS last_inspected,
       m.status          AS status,
       m.source_document AS source,
       m.page_number     AS page
ORDER BY m.inspection_date DESC
LIMIT 1
"""

# Dedup check: P-102 must be a single node despite appearing in report + work order.
P102_COUNT = "MATCH (e:Equipment {tag: 'P-102'}) RETURN count(e) AS c"

# F4 cross-document path: technician -> equipment -> substance -> regulation,
# spanning the maintenance report, the work order (HANDLES), and the SDS.
CROSS_DOC_PATH = """
MATCH (p:Person)<-[:PERFORMED_BY]-(:MaintenanceEvent)-[:PERFORMED_ON]->
      (e:Equipment)-[:HANDLES]->(s:Substance)-[:GOVERNED_BY]->(r:Regulation)
RETURN DISTINCT p.name AS technician, e.tag AS equipment,
       s.name AS substance, r.code AS regulation
LIMIT 10
"""


def main() -> None:
    driver = get_driver()
    with driver.session() as session:
        print("=== Phase 3 question: When is P-102's next inspection? ===")
        row = session.run(P102_NEXT_DUE).single()
        if row:
            print(f"  next_due_date : {row['next_due']}")
            print(f"  last_inspected: {row['last_inspected']}")
            print(f"  status        : {row['status']}")
            print(f"  citation      : {row['source']}, page {row['page']}")
        else:
            print("  (no result - is the graph populated?)")

        count = session.run(P102_COUNT).single()["c"]
        print(f"\n=== Dedup check: P-102 node count = {count} (expect 1) ===")

        print("\n=== F4 cross-document path (report -> work order -> SDS) ===")
        rows = session.run(CROSS_DOC_PATH).data()
        if not rows:
            print("  (no cross-doc path found)")
        for r in rows:
            print(
                f"  {r['technician']} maintains {r['equipment']} "
                f"which handles {r['substance']} governed by {r['regulation']}"
            )


if __name__ == "__main__":
    main()
