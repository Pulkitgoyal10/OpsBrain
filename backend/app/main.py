"""FastAPI app + routes.

Endpoints are thin wrappers around the existing pipeline/RAG modules - no
logic is reimplemented here. See CLAUDE.md B6 for the original endpoint spec;
routes here follow the current, more specific direction (POST /upload,
POST /chat, GET /graph, GET /compliance) rather than that draft's
POST /ingest, POST /ask, POST /compliance naming.
"""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from neo4j.exceptions import DriverError, Neo4jError

from app.compliance.agent import find_overdue_maintenance
from app.embeddings.qdrant_store import (
    count_chunks_by_filename,
    delete_chunks_by_filename,
    ensure_collection,
    get_client as get_qdrant_client,
)
from app.graph import schema as graph_schema
from app.graph.neo4j_store import (
    delete_document_graph,
    ensure_constraints,
    get_driver,
    write_graph,
)
from app.graph.pipeline import build_graph_from_document
from app.ingestion.loader_registry import SUPPORTED_SUFFIXES, get_loader_for_file
from app.ingestion.pipeline import ingest_pdf
from app.models.schemas import (
    ChatRequest,
    ChatResponse,
    CitationOut,
    ComplianceItemOut,
    ComplianceResponse,
    DocumentDeleteResponse,
    DocumentListResponse,
    DocumentOut,
    GraphContextItemOut,
    GraphEdgeOut,
    GraphNodeOut,
    GraphResponse,
    LocationFactOut,
    MaintenanceEventOut,
    SubstanceFactOut,
    SuggestedQuestionsResponse,
    UploadResponse,
)
from app.rag.answer import answer_question
from app.rag.groq_client import GroqRateLimitExhausted

logger = logging.getLogger(__name__)

# A reasonable subset for the graph explorer, per CLAUDE.md F-C - the whole
# graph would be small enough today, but this caps it independent of corpus
# growth. Not true importance-ranked sampling, just a hard node cap.
_GRAPH_QUERY_DEFAULT_LIMIT = 300


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One-time setup, not per-request: create the Qdrant collection and Neo4j
    # constraints if they don't already exist (both calls are idempotent).
    ensure_collection(get_qdrant_client())
    try:
        with get_driver().session() as session:
            ensure_constraints(session)
    except (DriverError, Neo4jError) as e:
        logger.warning("could not ensure Neo4j constraints at startup: %s", e)
    yield


app = FastAPI(title="OpsBrain API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # Local Next.js dev origins plus the deployed Vercel frontend
    # (CLAUDE.md B6: "CORS for the Vercel frontend").
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "https://opsbrain-eight.vercel.app",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/upload", response_model=UploadResponse)
async def upload(file: UploadFile):
    suffix = Path(file.filename).suffix.lower() if file.filename else ""
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type {suffix!r}. Supported: "
                f"{', '.join(sorted(SUPPORTED_SUFFIXES))} "
                "(scanned/image pages within a PDF, and standalone "
                ".png/.jpg/.jpeg images, are OCR'd automatically via "
                "OCR.space; .dxf CAD drawings are supported but .dwg is "
                "not - DWG is a proprietary binary format with no open "
                "parser, a deliberate limitation, not a gap)"
            ),
        )

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # Write under the real filename (sanitized to just the basename, so a
    # crafted filename like "../../etc/foo.pdf" can't escape tmp_dir) rather
    # than letting tempfile assign a random name - both ingest_pdf() (via the
    # chunker) and build_graph_from_document() derive source_filename/
    # Document.filename from path.name, and a random temp name there would
    # corrupt citations and break Neo4j's dedup (every re-upload of the same
    # file would get a different "filename", never matching its own natural
    # key).
    safe_filename = Path(file.filename).name
    tmp_dir = tempfile.mkdtemp()
    tmp_path = Path(tmp_dir) / safe_filename
    try:
        tmp_path.write_bytes(contents)
        loader = get_loader_for_file(tmp_path)

        try:
            chunks = ingest_pdf(loader, tmp_path)
        except Exception as e:
            # Most likely cause of a failure at the parsing/extraction stage
            # is a malformed file that slipped past the extension check, not
            # a server-side problem.
            raise HTTPException(status_code=400, detail=f"Could not process file: {e}") from e

        warnings: list[str] = []
        graph_node_counts: dict[str, int] = {}
        graph_nodes_created = 0
        graph_relationships_created = 0
        status = "success"

        try:
            # title=safe_filename (not the raw client-supplied file.filename):
            # this is the same name the Document node's natural key and every
            # citation returned by /chat will use, so the response and future
            # citations refer to the document consistently.
            nodes, rels = build_graph_from_document(
                loader, tmp_path, doc_type="uploaded", title=safe_filename
            )
            write_graph(nodes, rels)
            graph_nodes_created = len(nodes)
            graph_relationships_created = len(rels)
            for n in nodes:
                graph_node_counts[n.label] = graph_node_counts.get(n.label, 0) + 1
        except (DriverError, Neo4jError) as e:
            # Neo4j is a soft dependency here too (same philosophy as
            # app.rag.graph_lookup): the document's chunks are already
            # embedded and searchable, so a graph outage degrades this to a
            # partial success rather than failing the whole upload.
            logger.warning("graph write failed for %s: %s", safe_filename, e)
            warnings.append(f"Graph extraction/write failed: {e}")
            status = "partial"

        return UploadResponse(
            status=status,
            filename=safe_filename,
            doc_type="uploaded",
            chunks_created=len(chunks),
            graph_nodes_created=graph_nodes_created,
            graph_relationships_created=graph_relationships_created,
            graph_node_counts=graph_node_counts,
            warnings=warnings,
        )
    finally:
        os.unlink(tmp_path)
        os.rmdir(tmp_dir)


@app.get("/documents", response_model=DocumentListResponse)
def list_documents():
    """Every ingested Document node, regardless of which browser session
    uploaded it - lets the upload page populate its file list (and thus the
    delete button) for documents from previous sessions, not just ones
    uploaded since the current page load.
    """
    try:
        with get_driver().session() as session:
            records = session.run(
                "MATCH (d:Document) RETURN d.filename AS filename, d.doc_type AS doc_type, "
                "d.title AS title, d.ingested_at AS ingested_at ORDER BY d.ingested_at DESC"
            ).data()
    except (DriverError, Neo4jError) as e:
        raise HTTPException(status_code=500, detail=f"Document list query failed: {e}") from e

    return DocumentListResponse(
        documents=[
            DocumentOut(
                filename=r["filename"],
                doc_type=r["doc_type"],
                title=r["title"],
                ingested_at=r["ingested_at"],
            )
            for r in records
        ]
    )


@app.delete("/documents/{filename}", response_model=DocumentDeleteResponse)
def delete_document(filename: str):
    """Surgically retracts one document: its own chunks/nodes/edges are
    removed outright, but nodes/edges shared with a surviving document
    (app.graph.schema.MULTI_SOURCE_LABELS) are only trimmed - see
    app.graph.neo4j_store.delete_document_graph's docstring for the Cypher.

    "Exists" is checked independently on both sides, not just "Document node
    present" - POST /upload can return status="partial" (chunks embedded,
    graph write failed), leaving orphaned Qdrant chunks with no Document
    node at all, and those must still be deletable.
    """
    qdrant_client = get_qdrant_client()
    chunk_count = count_chunks_by_filename(qdrant_client, filename)

    try:
        with get_driver().session() as session:
            document_exists = (
                session.run(
                    "MATCH (d:Document {filename: $filename}) RETURN d LIMIT 1",
                    filename=filename,
                ).single()
                is not None
            )
    except (DriverError, Neo4jError) as e:
        raise HTTPException(status_code=500, detail=f"Existence check failed: {e}") from e

    if chunk_count == 0 and not document_exists:
        raise HTTPException(status_code=404, detail=f"No document found: {filename!r}")

    warnings: list[str] = []
    status = "success"

    try:
        delete_chunks_by_filename(qdrant_client, filename)
    except Exception as e:
        logger.warning("Qdrant delete failed for %s: %s", filename, e)
        warnings.append(f"Vector store deletion failed: {e}")
        status = "partial"

    graph_nodes_deleted = 0
    graph_nodes_updated = 0
    graph_relationships_deleted = 0
    try:
        summary = delete_document_graph(get_driver(), filename)
        graph_nodes_deleted = summary.nodes_deleted
        graph_nodes_updated = summary.nodes_updated
        graph_relationships_deleted = summary.relationships_deleted
    except (DriverError, Neo4jError) as e:
        logger.warning("graph delete failed for %s: %s", filename, e)
        warnings.append(f"Graph deletion failed: {e}")
        status = "partial"

    return DocumentDeleteResponse(
        status=status,
        filename=filename,
        chunks_deleted=chunk_count,
        graph_nodes_deleted=graph_nodes_deleted,
        graph_nodes_updated=graph_nodes_updated,
        graph_relationships_deleted=graph_relationships_deleted,
        warnings=warnings,
    )


def _graph_context_out(neighborhood) -> GraphContextItemOut:
    m = neighborhood.maintenance_event
    location = neighborhood.location
    return GraphContextItemOut(
        equipment_tag=neighborhood.tag,
        maintenance_event=(
            MaintenanceEventOut(
                inspection_date=m.inspection_date,
                next_due_date=m.next_due_date,
                status=m.status,
                issue_found=m.issue_found,
                action_taken=m.action_taken,
                source_document=m.source_document,
                page_number=m.page_number,
            )
            if m
            else None
        ),
        substances=[
            SubstanceFactOut(
                name=s.name,
                regulations=s.regulations,
                source_document=s.citation[0] if s.citation else None,
                page_number=s.citation[1] if s.citation else None,
            )
            for s in neighborhood.substances
        ],
        location=(
            LocationFactOut(
                name=location.name,
                source_document=location.citation[0] if location.citation else None,
                page_number=location.citation[1] if location.citation else None,
            )
            if location
            else None
        ),
    )


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    if not request.question or not request.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    try:
        result = answer_question(request.question, document=request.document)
    except GroqRateLimitExhausted as e:
        raise HTTPException(
            status_code=429, detail="API limit reached, please try again in a moment."
        ) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Answer synthesis failed: {e}") from e

    return ChatResponse(
        answer=result.answer,
        citations=[
            CitationOut(source_filename=c.source_filename, page_number=c.page_number)
            for c in result.citations
        ],
        graph_citations=[
            CitationOut(source_filename=c.source_filename, page_number=c.page_number)
            for c in result.graph_citations
        ],
        graph_context=[_graph_context_out(n) for n in result.graph_context],
    )


@app.get("/graph", response_model=GraphResponse)
def get_graph(limit: int = _GRAPH_QUERY_DEFAULT_LIMIT, document: str | None = None):
    try:
        with get_driver().session() as session:
            if document:
                # Scoped to one Document's own neighborhood, not the whole
                # database. Hop 1 is MENTIONS only, directed straight out of
                # d (Document -MENTIONS-> Equipment|Substance|Regulation|
                # Person, per app/graph/schema.py) - this deliberately
                # excludes walking a *different* document's own MENTIONS
                # edge into a shared entity (e.g. DocA -MENTIONS-> Equipment
                # <-MENTIONS- DocB), which an undirected multi-hop match
                # from d would otherwise do, pulling in unrelated documents.
                # Hops 2-3 extend from there via the remaining domain edges
                # (PERFORMED_ON/BY, LOCATED_IN, GOVERNED_BY, HANDLES) to
                # reach MaintenanceEvent/Location/etc - MENTIONS is the only
                # relationship type that ever touches a Document node, so
                # excluding it here makes it structurally impossible for
                # this traversal to reach any Document other than d.
                node_records = session.run(
                    "MATCH (d:Document {filename: $document}) "
                    "OPTIONAL MATCH (d)-[:MENTIONS]->(hop1) "
                    "WITH d, collect(DISTINCT hop1) AS hop1nodes "
                    "CALL { "
                    "  WITH hop1nodes "
                    "  UNWIND hop1nodes AS h1 "
                    "  OPTIONAL MATCH (h1)-[:PERFORMED_ON|PERFORMED_BY|LOCATED_IN|"
                    "GOVERNED_BY|HANDLES*1..2]-(hop23) "
                    "  RETURN collect(DISTINCT hop23) AS hop23nodes "
                    "} "
                    "WITH d, hop1nodes, hop23nodes "
                    "UNWIND ([d] + hop1nodes + hop23nodes) AS n "
                    "RETURN DISTINCT elementId(n) AS eid, labels(n)[0] AS label, "
                    "properties(n) AS props LIMIT $limit",
                    document=document,
                    limit=limit,
                ).data()
            else:
                node_records = session.run(
                    "MATCH (n) RETURN elementId(n) AS eid, labels(n)[0] AS label, "
                    "properties(n) AS props LIMIT $limit",
                    limit=limit,
                ).data()
            node_ids = [r["eid"] for r in node_records]
            edge_records = session.run(
                "MATCH (a)-[r]->(b) WHERE elementId(a) IN $ids AND elementId(b) IN $ids "
                "RETURN elementId(a) AS a_eid, elementId(b) AS b_eid, type(r) AS rel_type",
                ids=node_ids,
            ).data()
    except (DriverError, Neo4jError) as e:
        raise HTTPException(status_code=500, detail=f"Graph query failed: {e}") from e

    def node_id(label: str, props: dict) -> str:
        key_prop = graph_schema.NATURAL_KEY.get(label)
        key_value = props.get(key_prop, "") if key_prop else ""
        return f"{label}:{key_value}"

    eid_to_id = {r["eid"]: node_id(r["label"], r["props"]) for r in node_records}

    nodes = [
        GraphNodeOut(id=eid_to_id[r["eid"]], label=r["label"], properties=r["props"])
        for r in node_records
    ]
    edges = [
        GraphEdgeOut(
            source=eid_to_id[r["a_eid"]],
            target=eid_to_id[r["b_eid"]],
            type=r["rel_type"],
        )
        for r in edge_records
    ]

    return GraphResponse(nodes=nodes, edges=edges)


@app.get("/suggested-questions", response_model=SuggestedQuestionsResponse)
def get_suggested_questions():
    """Chat page empty-state chips, built from real ingested entities
    instead of fixed text. Up to 3 questions - two Equipment-tag templates
    (reusing the same tag if only one exists, otherwise a second distinct
    one for variety) and one Substance template - each included only if
    the graph actually has that entity type. An empty/near-empty graph
    degrades to an empty list, not an error, matching the frontend's
    generic-prompt fallback for "nothing to suggest yet".
    """
    try:
        with get_driver().session() as session:
            equipment_tags = [
                r["tag"]
                for r in session.run(
                    "MATCH (e:Equipment) WHERE e.tag IS NOT NULL "
                    "RETURN DISTINCT e.tag AS tag LIMIT 2"
                ).data()
            ]
            substances = [
                r["name"]
                for r in session.run(
                    "MATCH (s:Substance) WHERE s.name IS NOT NULL "
                    "RETURN DISTINCT s.name AS name LIMIT 1"
                ).data()
            ]
    except (DriverError, Neo4jError) as e:
        raise HTTPException(
            status_code=500, detail=f"Suggested-questions query failed: {e}"
        ) from e

    questions: list[str] = []
    if equipment_tags:
        questions.append(f"When is {equipment_tags[0]} due for its next inspection?")
    if substances:
        questions.append(f"What safety regulations apply to {substances[0]}?")
    if equipment_tags:
        second_tag = equipment_tags[1] if len(equipment_tags) > 1 else equipment_tags[0]
        questions.append(f"Who performed the last maintenance on {second_tag}?")

    return SuggestedQuestionsResponse(questions=questions)


@app.get("/compliance", response_model=ComplianceResponse)
def get_compliance(document: str | None = None):
    # Not wrapped in a soft-fallback try/except like /graph's read path: a
    # failed compliance check must surface as an error, not silently return
    # an empty "no gaps found" list, which would be misleading here.
    try:
        gaps = find_overdue_maintenance(document=document)
    except (DriverError, Neo4jError) as e:
        raise HTTPException(status_code=500, detail=f"Compliance check failed: {e}") from e

    items = [
        ComplianceItemOut(
            equipment_tag=g.equipment_tag,
            location=g.location,
            next_due_date=g.next_due_date,
            days_overdue=g.days_overdue,
            severity=g.severity,
            issue_found=g.issue_found,
            source_document=g.source_document,
            page_number=g.page_number,
        )
        for g in gaps
    ]

    if not items:
        summary = "No overdue maintenance found."
    else:
        high = sum(1 for g in gaps if g.severity == "high")
        medium = sum(1 for g in gaps if g.severity == "medium")
        low = sum(1 for g in gaps if g.severity == "low")
        summary = f"{len(items)} overdue maintenance item(s): {high} high, {medium} medium, {low} low severity."

    return ComplianceResponse(items=items, summary=summary)
