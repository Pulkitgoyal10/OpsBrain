"""Compliance gap agent (CLAUDE.md B5) - first real version.

Scoped to overdue maintenance: queries Neo4j for MaintenanceEvent nodes
already flagged 'overdue' (computed at graph-extraction time, see
app.graph.extractor._event_status), joins each to its Equipment and
Location, and computes a fresh days_overdue/severity as of today.

The fuller B5 vision (regulatory clause vs. procedure matching) needs
Procedure nodes we don't have yet - this is the piece we can build
correctly from what's actually in the graph right now.

Errors are NOT caught here (unlike app.rag.graph_lookup's soft-dependency
pattern) - a failed compliance check must surface as a failure, not
silently return an empty "no gaps found" list, which would be actively
misleading for a safety-relevant result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.graph.neo4j_store import get_driver

_OVERDUE_QUERY = """
MATCH (m:MaintenanceEvent {status: 'overdue'})-[:PERFORMED_ON]->(e:Equipment)
WHERE $document IS NULL OR m.source_document = $document
OPTIONAL MATCH (e)-[:LOCATED_IN]->(l:Location)
RETURN e.tag AS equipment_tag,
       l.name AS location,
       m.next_due_date AS next_due_date,
       m.issue_found AS issue_found,
       m.source_document AS source_document,
       m.page_number AS page_number
ORDER BY m.next_due_date ASC
"""

# >90 days overdue = high, 30-90 = medium, <30 = low.
_HIGH_SEVERITY_DAYS = 90
_MEDIUM_SEVERITY_DAYS = 30


@dataclass(frozen=True)
class ComplianceGap:
    equipment_tag: str
    location: str | None
    next_due_date: str
    days_overdue: int
    severity: str  # high | medium | low
    issue_found: str
    source_document: str
    page_number: int | None


def _severity(days_overdue: int) -> str:
    if days_overdue > _HIGH_SEVERITY_DAYS:
        return "high"
    if days_overdue >= _MEDIUM_SEVERITY_DAYS:
        return "medium"
    return "low"


def find_overdue_maintenance(document: str | None = None) -> list[ComplianceGap]:
    """Every MaintenanceEvent flagged overdue, with days_overdue/severity
    computed fresh as of today (not cached from extraction time).

    document: restricts to overdue events sourced from this one document
    (m.source_document), mirroring GET /graph?document=...'s and
    ChatRequest.document's scoping. None (default) checks the whole graph.
    """
    with get_driver().session() as session:
        records = session.run(_OVERDUE_QUERY, document=document).data()

    today = date.today()
    gaps = []
    for r in records:
        due = date.fromisoformat(r["next_due_date"])
        days_overdue = (today - due).days
        gaps.append(
            ComplianceGap(
                equipment_tag=r["equipment_tag"],
                location=r["location"],
                next_due_date=r["next_due_date"],
                days_overdue=days_overdue,
                severity=_severity(days_overdue),
                issue_found=r["issue_found"],
                source_document=r["source_document"],
                page_number=r["page_number"],
            )
        )
    return gaps
