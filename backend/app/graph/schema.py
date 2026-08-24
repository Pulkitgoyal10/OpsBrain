"""Knowledge graph schema: node labels, relationship types, natural keys.

Single source of truth for the OpsBrain graph (CLAUDE.md F3/F4, B3). Kept
intentionally small: 7 node labels, 6 core relationship types. See the approved
design doc for rationale (why MaintenanceEvent is reified, why dates are
properties not nodes, etc.).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Shared between extraction (normalizing a tag found inside a longer prose
# mention, e.g. "Pump P-102" -> "P-102") and question-time entity detection
# (scanning a user question for tags). One definition so the two never drift.
EQUIPMENT_TAG_PATTERN = re.compile(r"\b([A-Z]{1,4}-\d{2,4})\b")

# --- Node labels ---
DOCUMENT = "Document"
EQUIPMENT = "Equipment"
MAINTENANCE_EVENT = "MaintenanceEvent"
PERSON = "Person"
SUBSTANCE = "Substance"
REGULATION = "Regulation"
LOCATION = "Location"

NODE_LABELS = frozenset(
    {DOCUMENT, EQUIPMENT, MAINTENANCE_EVENT, PERSON, SUBSTANCE, REGULATION, LOCATION}
)

# --- Relationship types ---
PERFORMED_ON = "PERFORMED_ON"  # (MaintenanceEvent)->(Equipment)
PERFORMED_BY = "PERFORMED_BY"  # (MaintenanceEvent)->(Person)
LOCATED_IN = "LOCATED_IN"  # (Equipment)->(Location)
MENTIONS = "MENTIONS"  # (Document)->(Equipment|Substance|Regulation|Person)
GOVERNED_BY = "GOVERNED_BY"  # (Substance|Equipment)->(Regulation)
HANDLES = "HANDLES"  # (Equipment)->(Substance)

RELATIONSHIP_TYPES = frozenset(
    {PERFORMED_ON, PERFORMED_BY, LOCATED_IN, MENTIONS, GOVERNED_BY, HANDLES}
)

# --- Natural keys (the property MERGE dedups on, so an entity mentioned across
# many chunks/docs collapses to a single node). ---
NATURAL_KEY: dict[str, str] = {
    DOCUMENT: "filename",
    EQUIPMENT: "tag",
    MAINTENANCE_EVENT: "event_id",
    PERSON: "name",
    # Dedup Substance on name, not cas_number: prose mentions frequently omit
    # the CAS number, so name is the reliably-present key. cas_number is kept
    # as an enriching property when available.
    SUBSTANCE: "name",
    REGULATION: "code",
    LOCATION: "name",
}

# Labels whose nodes can be MERGEd across multiple source documents (e.g. an
# Equipment tag or Substance name mentioned in several docs collapses to one
# node) - these get an accumulated `sources` list instead of a single
# `source_document` scalar, which would otherwise be silently overwritten by
# whichever extraction call runs last. Document and MaintenanceEvent are
# excluded: a Document's key IS the document, and a MaintenanceEvent's key
# (equipment tag + inspection date) is written by exactly one extraction pass
# in practice, so neither is ever re-merged with a conflicting source.
MULTI_SOURCE_LABELS = frozenset({EQUIPMENT, PERSON, SUBSTANCE, REGULATION, LOCATION})


@dataclass
class GraphNode:
    """A node to MERGE. `properties` must contain the label's NATURAL_KEY."""

    label: str
    properties: dict

    @property
    def key_value(self):
        return self.properties[NATURAL_KEY[self.label]]


@dataclass
class GraphRelationship:
    """An edge to MERGE, identifying endpoints by their natural-key values."""

    type: str
    start_label: str
    start_key: str
    end_label: str
    end_key: str
    properties: dict = field(default_factory=dict)


def constraint_statements() -> list[str]:
    """Cypher to enforce one node per natural key. Idempotent (IF NOT EXISTS)."""
    statements = []
    for label, key in NATURAL_KEY.items():
        constraint_name = f"unique_{label.lower()}_{key}"
        statements.append(
            f"CREATE CONSTRAINT {constraint_name} IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.{key} IS UNIQUE"
        )
    return statements
