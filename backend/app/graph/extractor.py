"""Entity/relation extraction into graph nodes+edges (CLAUDE.md B3).

Hybrid, per the approved design:
- extract_from_table(): deterministic parse of the maintenance inspection table
  (Equipment / MaintenanceEvent / Person / Location). No LLM - directly applies
  the Phase 3.5 lesson that LLMs mishandle dense tabular rows.
- extract_from_prose(): LLM (Groq) structured JSON extraction over prose chunks
  (SDS sections, work order) for Substance / Regulation / Equipment / Person /
  Location entities and HANDLES / GOVERNED_BY / LOCATED_IN relationships.
"""

from __future__ import annotations

import json
import re
from datetime import date

from app.graph import schema
from app.graph.schema import GraphNode, GraphRelationship
from app.ingestion.base import Chunk
from app.rag.groq_client import chat

# --- Deterministic table extraction ---------------------------------------

_MAINT_HEADER = [
    "Equipment ID",
    "Inspection Date",
    "Technician",
    "Issue Found",
    "Action Taken",
    "Next Due Date",
]

# Equipment tag prefix -> class, matching the asset families named in the
# report's own summary (pump, valve, compressor, heat exchanger, tank, boiler, fan).
_EQUIP_CLASS = {
    "P": "pump",
    "PMP": "pump",
    "V": "valve",
    "CV": "control valve",
    "C": "compressor",
    "HX": "heat exchanger",
    "T": "tank",
    "BLR": "boiler",
    "FN": "fan",
}

_FACILITY_RE = re.compile(r"Facility:\s*(.+)")
_DUE_SOON_DAYS = 30


def _equipment_class(tag: str) -> str:
    prefix = tag.split("-")[0].upper()
    return _EQUIP_CLASS.get(prefix, "unknown")


def _normalize_tag(name: str) -> str:
    """Pull the canonical equipment tag (e.g. 'P-102') out of a prose mention
    like 'Pump P-102', so it dedups against the table-extracted Equipment node.
    Falls back to the upper-cased name when no tag pattern is present.
    """
    match = schema.EQUIPMENT_TAG_PATTERN.search(name.upper())
    return match.group(1) if match else name.upper()


def _is_maintenance_header(row: list) -> bool:
    return [(c or "").strip() for c in row][:6] == _MAINT_HEADER


def has_maintenance_table(tables: list[tuple[int | None, list[list[str | None]]]]) -> bool:
    """True if any extracted table matches the maintenance inspection header.

    Lets a caller (e.g. app.graph.pipeline) auto-detect table vs. prose
    extraction mode for an arbitrary uploaded document, instead of hardcoding
    the mode per filename.
    """
    return any(rows and _is_maintenance_header(rows[0]) for _page_no, rows in tables)


def _event_status(next_due: str) -> str:
    try:
        due = date.fromisoformat(next_due)
    except (ValueError, TypeError):
        return "unknown"
    delta = (due - date.today()).days
    if delta < 0:
        return "overdue"
    if delta <= _DUE_SOON_DAYS:
        return "due"
    return "ok"


def _find_facility(text: str) -> str | None:
    match = _FACILITY_RE.search(text)
    return match.group(1).strip() if match else None


def extract_from_table(
    tables: list[tuple[int | None, list[list[str | None]]]],
    *,
    source_filename: str,
    full_text: str,
    chunk_ids_by_page: dict[int | None, list[str]],
) -> tuple[list[GraphNode], list[GraphRelationship]]:
    """Parse maintenance inspection tables into nodes + relationships.

    Only tables whose header matches the maintenance columns are parsed; other
    tables (e.g. the SDS regulatory tables) are skipped and left to the prose path.
    """
    nodes: list[GraphNode] = []
    rels: list[GraphRelationship] = []

    location = _find_facility(full_text)
    if location:
        nodes.append(
            GraphNode(
                schema.LOCATION,
                {"name": location, "source_document": source_filename},
            )
        )

    for page_no, rows in tables:
        if not rows or not _is_maintenance_header(rows[0]):
            continue
        page_chunk_ids = chunk_ids_by_page.get(page_no, [])

        for row in rows[1:]:
            cells = [(c or "").strip() for c in row]
            if len(cells) < 6 or _is_maintenance_header(row):
                continue
            tag, insp, tech, issue, action, next_due = cells[:6]
            if not tag:
                continue
            tag = tag.upper()

            nodes.append(
                GraphNode(
                    schema.EQUIPMENT,
                    {
                        "tag": tag,
                        "equipment_class": _equipment_class(tag),
                        "source_document": source_filename,
                        "source_chunk_ids": page_chunk_ids,
                    },
                )
            )

            event_id = f"maint_{tag}_{insp}"
            nodes.append(
                GraphNode(
                    schema.MAINTENANCE_EVENT,
                    {
                        "event_id": event_id,
                        "inspection_date": insp,
                        "next_due_date": next_due,
                        "issue_found": issue,
                        "action_taken": action,
                        "status": _event_status(next_due),
                        "page_number": page_no,
                        "source_document": source_filename,
                        "source_chunk_ids": page_chunk_ids,
                    },
                )
            )
            rels.append(
                GraphRelationship(
                    schema.PERFORMED_ON,
                    schema.MAINTENANCE_EVENT,
                    event_id,
                    schema.EQUIPMENT,
                    tag,
                )
            )

            if tech:
                nodes.append(
                    GraphNode(
                        schema.PERSON,
                        {
                            "name": tech,
                            "role": "technician",
                            "source_document": source_filename,
                            "source_chunk_ids": page_chunk_ids,
                        },
                    )
                )
                rels.append(
                    GraphRelationship(
                        schema.PERFORMED_BY,
                        schema.MAINTENANCE_EVENT,
                        event_id,
                        schema.PERSON,
                        tech,
                    )
                )

            if location:
                rels.append(
                    GraphRelationship(
                        schema.LOCATED_IN,
                        schema.EQUIPMENT,
                        tag,
                        schema.LOCATION,
                        location,
                    )
                )

    return nodes, rels


# --- LLM prose extraction --------------------------------------------------

# Curated allowlist of real regulatory frameworks/standards (CLAUDE.md names
# OISD and the Factory Act specifically as target regulations for this
# project, even though the current sample docs don't mention them). Matched
# as a case-insensitive substring against the candidate name, in either
# direction, so aliases/full names ("OSHA Hazard Communication Standard" vs
# "OSHA HazCom") both match one canonical entry. Deliberately does NOT
# include bare terms like "OSHA" or "EPA" alone - that would also match junk
# like "OSHA PEL" (an exposure limit value, not a regulation) or "EPA
# Statement" (a document section label), which is exactly the noise this is
# meant to filter out.
_REGULATION_ALLOWLIST = [
    "OSHA HazCom",
    "Hazard Communication",
    "CERCLA",
    "Comprehensive Environmental Response",
    "TSCA",
    "Toxic Substances Control Act",
    "SARA 313",
    "Superfund Amendments and Reauthorization Act",
    "WHMIS",
    "Workplace Hazardous Materials Information System",
    "California Proposition 65",
    "Proposition 65",
    "Prop 65",
    "DSL",
    "NDSL",
    "Domestic Substances List",
    "Clean Water Act",
    "RCRA",
    "Resource Conservation and Recovery Act",
    "EPCRA",
    "Emergency Planning and Community Right-to-Know Act",
    "Clean Air Act",
    "OISD",
    "Factory Act",
]

# A "clear regulation-code pattern": CFR citations (e.g. "29 CFR 1910.1200",
# "40 CFR 302") and OISD standard numbers (e.g. "OISD-132"), which don't need
# to be individually enumerated in the allowlist above.
_REGULATION_CODE_PATTERN = re.compile(
    r"\b\d{1,3}\s*CFR\s*\d+(?:\.\d+)?(?:\([a-zA-Z0-9]+\))*\b|\bOISD[- ]?\d{2,4}\b",
    re.IGNORECASE,
)


def _is_legitimate_regulation(name: str) -> bool:
    """Reject LLM-extracted "Regulation" candidates that aren't a real named
    regulatory framework/standard or a recognizable regulation-code citation
    - filters out exposure-limit values (OSHA PEL, NIOSH IDLH), reportable
    quantities (RQ 100 lb), rating systems (NFPA, HMIS), agency names
    (MSHA/NIOSH), and vague headers ("Current local regulations") that the
    LLM extracts despite the prompt's negative instructions against them.
    """
    if _REGULATION_CODE_PATTERN.search(name):
        return True
    # Forward direction only (allowlist term must appear IN the candidate) -
    # matching in the other direction too let short junk terms like "HMIS"
    # pass by being an accidental substring of a legitimate longer term
    # ("WHMIS"). Forward-only doesn't have that failure mode.
    name_lower = name.lower()
    return any(term.lower() in name_lower for term in _REGULATION_ALLOWLIST)


_PROSE_ENTITY_TYPES = ["Equipment", "Substance", "Regulation", "Person", "Location"]
_PROSE_REL_TYPES = ["HANDLES", "GOVERNED_BY", "LOCATED_IN"]

_PROSE_SYSTEM = (
    "You extract a knowledge graph from an industrial document excerpt. "
    "Respond with JSON only, no prose. Use exactly this shape: "
    '{"entities": [{"type": "<type>", "name": "<canonical name or tag>", '
    '"cas_number": "<substances only, if explicitly present>", '
    '"code": "<regulations only, if explicitly present>"}], '
    '"relationships": [{"type": "<type>", "source": "<entity name>", '
    '"target": "<entity name>"}]}. '
    f"Allowed entity types: {', '.join(_PROSE_ENTITY_TYPES)}. "
    "Equipment = tags like P-102, V-204. Substance = chemicals. "
    "Regulation = codes/standards like OSHA HazCom, CERCLA, TSCA. "
    "Person = named individuals. Location = facilities/plants/units. "
    f"Allowed relationship types: {', '.join(_PROSE_REL_TYPES)}. "
    "HANDLES: Equipment->Substance. GOVERNED_BY: Substance or Equipment->Regulation. "
    "LOCATED_IN: Equipment->Location. "
    "A Substance must be a named chemical (e.g. Sodium hypochlorite). Do NOT "
    "extract product use-descriptions (e.g. 'household bleach'), physical or "
    "chemical properties (e.g. 'strong oxidizer', 'corrosive'), or mixtures of "
    "products as substances. A Regulation must be a named law, standard, or code "
    "(e.g. OSHA HazCom, CERCLA, TSCA, SARA 313, WHMIS, California Proposition 65). "
    "Do NOT extract exposure-limit acronyms (TLV, PEL, IDLH), reportable "
    "quantities (e.g. 'RQ 100 lb'), hazard-rating systems (NFPA, HMIS), or "
    "company/supplier names as regulations. "
    "Extract ONLY entities/relationships explicitly stated in the excerpt. "
    "relationship source/target must be names that appear in your entities list. "
    "If nothing is present, return empty arrays."
)

# Canonical orientation per relationship type: (allowed start labels, allowed
# end labels). We enforce direction in code rather than trusting the LLM's
# source/target ordering, which is unreliable on dense text.
_REL_ORIENTATION: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    schema.HANDLES: (frozenset({schema.EQUIPMENT}), frozenset({schema.SUBSTANCE})),
    schema.GOVERNED_BY: (
        frozenset({schema.SUBSTANCE, schema.EQUIPMENT}),
        frozenset({schema.REGULATION}),
    ),
    schema.LOCATED_IN: (frozenset({schema.EQUIPMENT}), frozenset({schema.LOCATION})),
}


def _orient(
    rtype: str, a_label: str, a_key: str, b_label: str, b_key: str
) -> tuple[str, str, str, str] | None:
    """Return (start_label, start_key, end_label, end_key) in the schema's
    canonical direction, flipping the LLM's ordering if needed, or None if the
    endpoint labels don't fit the relationship at all.
    """
    starts, ends = _REL_ORIENTATION[rtype]
    if a_label in starts and b_label in ends:
        return a_label, a_key, b_label, b_key
    if b_label in starts and a_label in ends:
        return b_label, b_key, a_label, a_key
    return None


def _normalize_substance(name: str) -> str:
    s = " ".join(name.split())
    return s[:1].upper() + s[1:].lower() if s else s


def _parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        return json.loads(match.group(0)) if match else {"entities": [], "relationships": []}


def _entity_to_node(ent: dict, chunk: Chunk) -> GraphNode | None:
    """Map one LLM entity dict to a GraphNode with the correct natural key."""
    etype = (ent.get("type") or "").strip()
    name = (ent.get("name") or "").strip()
    if etype not in schema.NODE_LABELS or not name:
        return None

    prov = {
        "source_document": chunk.metadata.source_filename,
        "source_chunk_ids": [chunk.metadata.chunk_id],
    }

    if etype == schema.EQUIPMENT:
        tag = _normalize_tag(name)
        return GraphNode(etype, {"tag": tag, "equipment_class": _equipment_class(tag), **prov})
    if etype == schema.SUBSTANCE:
        props = {"name": _normalize_substance(name), **prov}
        if ent.get("cas_number"):
            props["cas_number"] = ent["cas_number"].strip()
        return GraphNode(etype, props)
    if etype == schema.REGULATION:
        code = (ent.get("code") or name).strip()
        if not _is_legitimate_regulation(code) and not _is_legitimate_regulation(name):
            return None
        return GraphNode(etype, {"code": code, "title": name, **prov})
    if etype == schema.PERSON:
        return GraphNode(etype, {"name": name, **prov})
    if etype == schema.LOCATION:
        return GraphNode(etype, {"name": name, **prov})
    return None


def extract_from_prose(chunk: Chunk) -> tuple[list[GraphNode], list[GraphRelationship]]:
    """LLM-extract entities/relationships from one prose chunk."""
    raw = chat(
        [
            {"role": "system", "content": _PROSE_SYSTEM},
            {"role": "user", "content": chunk.text},
        ],
        temperature=0.0,
        max_tokens=1024,
        response_format={"type": "json_object"},
    )
    data = _parse_json(raw)

    nodes: list[GraphNode] = []
    # name (as the LLM wrote it) -> (label, natural-key value), for resolving rels
    name_index: dict[str, tuple[str, str]] = {}
    for ent in data.get("entities", []):
        node = _entity_to_node(ent, chunk)
        if node is None:
            continue
        nodes.append(node)
        name_index[(ent.get("name") or "").strip()] = (node.label, node.key_value)

    rels: list[GraphRelationship] = []
    for rel in data.get("relationships", []):
        rtype = (rel.get("type") or "").strip()
        src = (rel.get("source") or "").strip()
        tgt = (rel.get("target") or "").strip()
        if rtype not in _REL_ORIENTATION:
            continue
        if src not in name_index or tgt not in name_index:
            continue  # drop edges referencing entities the model didn't list
        a_label, a_key = name_index[src]
        b_label, b_key = name_index[tgt]
        oriented = _orient(rtype, a_label, a_key, b_label, b_key)
        if oriented is None:
            continue  # endpoint labels don't fit this relationship - drop it
        start_label, start_key, end_label, end_key = oriented
        rels.append(
            GraphRelationship(rtype, start_label, start_key, end_label, end_key)
        )

    return nodes, rels
