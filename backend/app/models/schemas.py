"""Pydantic request/response models for the API layer.

Kept separate from the internal dataclasses in app.rag.answer / app.graph.schema
so the public API contract doesn't change shape just because an internal
implementation detail does.
"""

from __future__ import annotations

from pydantic import BaseModel


class CitationOut(BaseModel):
    source_filename: str
    page_number: int | None


# --- POST /chat ---


class ChatRequest(BaseModel):
    question: str
    # Restricts retrieval (both vector search and graph lookup) to this
    # document's own content when set, mirroring GET /graph?document=...'s
    # scoping. Omitted/null searches everything, unchanged from before.
    document: str | None = None


class MaintenanceEventOut(BaseModel):
    inspection_date: str
    next_due_date: str
    status: str
    issue_found: str
    action_taken: str
    source_document: str
    page_number: int | None


class SubstanceFactOut(BaseModel):
    name: str
    regulations: list[str]
    source_document: str | None
    page_number: int | None


class LocationFactOut(BaseModel):
    name: str
    source_document: str | None
    page_number: int | None


class GraphContextItemOut(BaseModel):
    equipment_tag: str
    maintenance_event: MaintenanceEventOut | None
    substances: list[SubstanceFactOut]
    location: LocationFactOut | None


class ChatResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    graph_citations: list[CitationOut]
    # The graph facts actually retrieved and given to the LLM this turn, not
    # filtered to what it cited (graph_citations already covers that) - the
    # frontend's future "graph context used" strip (CLAUDE.md F-B) needs the
    # full picture, e.g. to show a collapsible panel of everything the graph
    # contributed even if the answer text only referenced part of it.
    graph_context: list[GraphContextItemOut]


# --- POST /upload ---


class UploadResponse(BaseModel):
    status: str  # "success" | "partial" (graph write failed but ingestion succeeded)
    filename: str
    doc_type: str
    chunks_created: int
    graph_nodes_created: int
    graph_relationships_created: int
    graph_node_counts: dict[str, int]
    warnings: list[str]


# --- GET /graph ---


class GraphNodeOut(BaseModel):
    id: str  # "{label}:{natural_key_value}", globally unique across labels
    label: str
    properties: dict


class GraphEdgeOut(BaseModel):
    source: str
    target: str
    type: str


class GraphResponse(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]


# --- GET /documents ---


class DocumentOut(BaseModel):
    filename: str
    doc_type: str
    title: str
    ingested_at: str


class DocumentListResponse(BaseModel):
    documents: list[DocumentOut]


# --- GET /suggested-questions ---


class SuggestedQuestionsResponse(BaseModel):
    questions: list[str]


# --- GET /compliance ---
# v1 shape: overdue-maintenance gaps (app.compliance.agent). Deviates from
# CLAUDE.md B5's originally-sketched {regulation, status, evidence,
# linked_procedure} shape, which needs Procedure nodes we don't have yet.


class ComplianceItemOut(BaseModel):
    equipment_tag: str
    location: str | None
    next_due_date: str
    days_overdue: int
    severity: str  # high | medium | low
    issue_found: str
    source_document: str
    page_number: int | None


class ComplianceResponse(BaseModel):
    items: list[ComplianceItemOut]
    summary: str


# --- DELETE /documents/{filename} ---


class DocumentDeleteResponse(BaseModel):
    status: str  # "success" | "partial" (Qdrant/Neo4j disagree on outcome)
    filename: str
    chunks_deleted: int
    graph_nodes_deleted: int
    graph_nodes_updated: int  # shared nodes trimmed, not deleted, since another document still needs them
    graph_relationships_deleted: int
    warnings: list[str]
