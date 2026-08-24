"""Graph-side lookups for RAG answer synthesis (F4 / CLAUDE.md B4 step 2).

Detects equipment tags in a question and pulls a 1-2 hop neighborhood from
Neo4j: the equipment's most recent MaintenanceEvent (1 hop), the Substance(s)
it HANDLES and each one's GOVERNED_BY Regulations (2 hops), and its
LOCATED_IN Location (1 hop). This is what closes the Phase 3 gap for direct
lookups (P-102's next due date) *and* makes relational questions spanning
technician -> equipment -> substance -> regulation answerable live through
/chat, not just in scripts/test_graph_query.py's standalone reference query.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from neo4j.exceptions import DriverError, Neo4jError

from app.graph import schema
from app.graph.neo4j_store import get_driver

logger = logging.getLogger(__name__)

_MAINTENANCE_EVENT_QUERY = """
MATCH (e:Equipment {tag: $tag})<-[:PERFORMED_ON]-(m:MaintenanceEvent)
WHERE $document IS NULL OR m.source_document = $document
RETURN m.inspection_date AS inspection_date,
       m.next_due_date   AS next_due_date,
       m.status          AS status,
       m.issue_found     AS issue_found,
       m.action_taken    AS action_taken,
       m.source_document AS source_document,
       m.page_number     AS page_number
ORDER BY m.inspection_date DESC
LIMIT 1
"""

# Substance/Location nodes are MERGEd across documents (schema.
# MULTI_SOURCE_LABELS), so app.graph.neo4j_store.merge_node() accumulates a
# `sources` list on them instead of a single `source_document` scalar, which
# would otherwise be silently overwritten by whichever extraction call ran
# last. Citations here read that accumulated list directly. Document-scoping
# (below) checks the same list for a "filename|page|chunk_id" entry starting
# with the requested filename, since that's the only place a Substance's
# provenance is recorded.
_SUBSTANCES_QUERY = """
MATCH (e:Equipment {tag: $tag})-[:HANDLES]->(s:Substance)
WHERE $document IS NULL OR ANY(src IN s.sources WHERE src STARTS WITH $document + '|')
OPTIONAL MATCH (s)-[:GOVERNED_BY]->(r:Regulation)
RETURN s.name AS name,
       s.sources AS sources,
       collect(DISTINCT r.code) AS regulations
"""

_LOCATION_QUERY = """
MATCH (e:Equipment {tag: $tag})-[:LOCATED_IN]->(l:Location)
WHERE $document IS NULL OR ANY(src IN l.sources WHERE src STARTS WITH $document + '|')
RETURN l.name AS name, l.sources AS sources
LIMIT 1
"""

_EQUIPMENT_EXISTS_QUERY = "MATCH (e:Equipment {tag: $tag}) RETURN e.tag AS tag LIMIT 1"

# Document-scoping's first gate: the equipment must actually be MENTIONS-ed
# by the requested document (mirrors GET /graph?document=...'s hop-1 rule -
# MENTIONS is the only relationship type that ever touches a Document node)
# before any of its facts are considered part of that document's scope at
# all, even if e.g. its MaintenanceEvent happens to match by coincidence.
_EQUIPMENT_MENTIONED_BY_DOCUMENT_QUERY = """
MATCH (d:Document {filename: $document})-[:MENTIONS]->(e:Equipment {tag: $tag})
RETURN e.tag AS tag LIMIT 1
"""


def _first_citation(sources: list[str] | None) -> tuple[str, int | None] | None:
    """(filename, page) from the first accumulated source, or None if the
    node has none recorded. Each entry is "filename|page|chunk_id"
    (app.graph.neo4j_store._encode_source's flattened format - Neo4j
    properties can't store nested/mapped values). page is the literal
    string "None" for a non-paginated source document (e.g. a spreadsheet
    row), decoded back to Python None here.
    """
    if not sources:
        return None
    filename, page, _chunk_id = sources[0].split("|", 2)
    return filename, (None if page == "None" else int(page))


@dataclass(frozen=True)
class EquipmentFact:
    tag: str
    inspection_date: str
    next_due_date: str
    status: str
    issue_found: str
    action_taken: str
    source_document: str
    page_number: int | None


@dataclass(frozen=True)
class SubstanceFact:
    name: str
    regulations: list[str]
    citation: tuple[str, int | None] | None  # (filename, page), or None if unresolvable


@dataclass(frozen=True)
class LocationFact:
    name: str
    citation: tuple[str, int | None] | None


@dataclass(frozen=True)
class EquipmentNeighborhood:
    tag: str
    maintenance_event: EquipmentFact | None
    substances: list[SubstanceFact]
    location: LocationFact | None


def find_equipment_tags(text: str) -> list[str]:
    """Deduped, order-preserving equipment tags found in free text."""
    seen: set[str] = set()
    tags: list[str] = []
    for tag in schema.EQUIPMENT_TAG_PATTERN.findall(text.upper()):
        if tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def lookup_equipment_fact(tag: str, document: str | None = None) -> EquipmentNeighborhood | None:
    """The equipment's 1-2 hop neighborhood: its most recent MaintenanceEvent,
    the Substance(s) it HANDLES (each with its GOVERNED_BY Regulations), and
    its LOCATED_IN Location. Returns None only if the tag isn't a known
    Equipment node at all; if it exists but some relationships are absent,
    the corresponding field is None/empty rather than dropping the whole
    lookup - a real equipment with a known location but no HANDLES edge
    shouldn't lose its location fact.

    When `document` is given, scopes to that document: the equipment must be
    MENTIONS-ed by it (or this returns None outright, same as an unknown
    tag), and each fact (MaintenanceEvent/Substance/Location) is only
    included if it was itself recorded by that document - an equipment
    mentioned in doc A but whose maintenance was recorded in doc B should not
    leak doc B's facts into a doc-A-scoped question.

    Neo4j is a soft dependency for answer quality, not a hard dependency for
    answer availability - any connectivity/config/query failure falls back to
    None (pure vector RAG picks up the slack) rather than raising.
    """
    try:
        with get_driver().session() as session:
            if session.run(_EQUIPMENT_EXISTS_QUERY, tag=tag).single() is None:
                return None
            if document is not None:
                mentioned = session.run(
                    _EQUIPMENT_MENTIONED_BY_DOCUMENT_QUERY, tag=tag, document=document
                ).single()
                if mentioned is None:
                    return None

            event_record = session.run(
                _MAINTENANCE_EVENT_QUERY, tag=tag, document=document
            ).single()
            substance_records = session.run(
                _SUBSTANCES_QUERY, tag=tag, document=document
            ).data()
            location_record = session.run(_LOCATION_QUERY, tag=tag, document=document).single()
    except (DriverError, Neo4jError, RuntimeError) as e:
        logger.warning("graph lookup failed for tag %s: %s", tag, e)
        return None

    maintenance_event = (
        EquipmentFact(
            tag=tag,
            inspection_date=event_record["inspection_date"],
            next_due_date=event_record["next_due_date"],
            status=event_record["status"],
            issue_found=event_record["issue_found"],
            action_taken=event_record["action_taken"],
            source_document=event_record["source_document"],
            page_number=event_record["page_number"],
        )
        if event_record is not None
        else None
    )

    substances = [
        SubstanceFact(
            name=r["name"],
            regulations=[code for code in r["regulations"] if code],
            citation=_first_citation(r["sources"]),
        )
        for r in substance_records
    ]

    location = (
        LocationFact(
            name=location_record["name"],
            citation=_first_citation(location_record["sources"]),
        )
        if location_record is not None
        else None
    )

    return EquipmentNeighborhood(
        tag=tag,
        maintenance_event=maintenance_event,
        substances=substances,
        location=location,
    )


def _citation_bracket(source_filename: str, page_number: int | None) -> str:
    """[filename, page N] for a paginated source, or [filename] when
    page_number is None (e.g. a spreadsheet row - no pages to cite).
    """
    if page_number is None:
        return f"[{source_filename}]"
    return f"[{source_filename}, page {page_number}]"


def format_graph_fact(neighborhood: EquipmentNeighborhood) -> str:
    """Render an equipment's neighborhood as authoritative context blocks for
    the LLM prompt. Every citable line uses the same bracket format as vector
    chunks (app.rag.answer._citation_bracket), so citation extraction needs
    no special-casing. Regulations are listed by name as enrichment, not
    individually cited - the citable fact is "this equipment handles this
    substance"; which regulations govern it is supporting detail from the
    same source.
    """
    lines = [
        f"VERIFIED GRAPH FACTS for {neighborhood.tag} (authoritative - use "
        "these values directly, do not second-guess, recompute, or "
        "cross-check them against other excerpts):"
    ]

    if neighborhood.maintenance_event:
        m = neighborhood.maintenance_event
        lines.append(
            f"- Maintenance: last inspection {m.inspection_date}, next due "
            f"{m.next_due_date}, status {m.status}. Issue found: "
            f"{m.issue_found}. Action taken: {m.action_taken}. "
            f"{_citation_bracket(m.source_document, m.page_number)}"
        )

    for s in neighborhood.substances:
        reg_part = f" Governed by: {', '.join(s.regulations)}." if s.regulations else ""
        citation = f" {_citation_bracket(*s.citation)}" if s.citation else ""
        lines.append(f"- Handles substance: {s.name}.{reg_part}{citation}")

    if neighborhood.location:
        l = neighborhood.location
        citation = f" {_citation_bracket(*l.citation)}" if l.citation else ""
        lines.append(f"- Located in: {l.name}.{citation}")

    return "\n".join(lines)
