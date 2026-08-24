"""Regression tests for four confirmed historical bugs, reconstructed from
git history (see commit messages cited in each test's docstring). Bug #4
(parallel-upload race condition, commit 26639d5) is intentionally excluded -
it was a frontend-only fix (frontend/app/upload/page.tsx) with no backend
code path to regress-test here.
"""

from __future__ import annotations

from app.graph import schema
from app.graph.neo4j_store import merge_node
from app.graph.schema import GraphNode
from app.rag.retrieval import RetrievedChunk
from tests.conftest import FakeResult, FakeSession

# --- Bug 1: temp-filename citation corruption (commit 37f9702) -------------


def test_upload_uses_sanitized_real_filename_not_a_random_temp_name(
    client,
    mock_loader,
    mock_embed_texts,
    mock_qdrant_client,
    mock_upsert_chunks,
    mock_build_graph_from_document,
    mock_write_graph,
):
    """/upload originally wrote the uploaded file under NamedTemporaryFile's
    random name, and both the ingestion chunker and build_graph_from_document
    derived source_filename/Document.filename from that on-disk name - so
    every citation was tagged with a meaningless temp name instead of the
    real filename, and re-uploading the same file never matched its own
    Neo4j natural key (duplicate Document nodes). Fixed by writing the temp
    file under the real filename, sanitized to just the basename (which also
    closes a path-traversal angle - tested here via a crafted filename).
    """
    response = client.post(
        "/upload",
        files={"file": ("../../etc/passwd.pdf", b"%PDF-1.4 fake bytes", "application/pdf")},
    )

    assert response.status_code == 200
    body = response.json()
    # No directory components leaked into the response's filename.
    assert body["filename"] == "passwd.pdf"

    # ingest_pdf ran for real (only its Cohere/Qdrant calls are mocked) - the
    # chunk it produced must be tagged with the sanitized real name, not
    # whatever name the on-disk temp file happened to get.
    _, upserted_chunks, _ = mock_upsert_chunks.call_args.args
    assert len(upserted_chunks) == 1
    assert upserted_chunks[0].metadata.source_filename == "passwd.pdf"

    # build_graph_from_document must receive a path whose name is the same
    # sanitized filename, and title (which becomes Document.filename) must
    # match it too - both derive from the temp file's on-disk name, exactly
    # what the original bug corrupted.
    call_args = mock_build_graph_from_document.call_args
    _passed_loader, passed_path = call_args.args
    assert passed_path.name == "passwd.pdf"
    assert call_args.kwargs["title"] == "passwd.pdf"


# --- Bug 2: Neo4j last-write-wins on multi-document nodes (commit ef83b9b) -


def test_merge_node_accumulates_sources_instead_of_overwriting_for_multi_source_labels():
    """merge_node() used to do a blind `SET n += $props` on every MERGE, so a
    Substance/Equipment/Person/Regulation/Location node mentioned across
    multiple documents had its source_document scalar silently overwritten
    by whichever extraction call ran last - corrupting any citation built
    from that property (e.g. a Substance whose 7-of-8 contributing chunks
    were from the SDS reporting source_document as the 1-page work order).
    The fix excludes source_document from the blind SET for
    schema.MULTI_SOURCE_LABELS and instead accumulates a `sources` list, one
    "filename|page|chunk_id" entry per contributing chunk.
    """
    session = FakeSession([FakeResult()])
    node = GraphNode(
        schema.SUBSTANCE,
        {
            "name": "Sodium hypochlorite",
            "source_document": "Sample Work Order.pdf",
            "source_chunk_ids": ["Sample Work Order_p1_0"],
        },
    )

    merge_node(session, node)

    assert len(session.queries) == 1
    query, params = session.queries[0]
    # source_document must never land in the blind SET n += $props for a
    # multi-source label - that's exactly the property last-write-wins used
    # to clobber.
    assert "source_document" not in params["props"]
    assert params["props"]["name"] == "Sodium hypochlorite"
    # Instead, provenance accumulates into n.sources.
    assert "n.sources" in query
    assert params["new_sources"] == ["Sample Work Order.pdf|1|Sample Work Order_p1_0"]


def test_merge_node_still_sets_source_document_for_single_source_labels():
    """Contrast case: Document and MaintenanceEvent are excluded from
    MULTI_SOURCE_LABELS because their natural keys are written by exactly
    one extraction pass in practice, so the plain scalar SET (not the
    accumulated-sources path) is still correct and expected for them.
    """
    session = FakeSession([FakeResult()])
    node = GraphNode(
        schema.DOCUMENT,
        {
            "filename": "Sample Work Order.pdf",
            "doc_type": "uploaded",
            "title": "Sample Work Order.pdf",
        },
    )

    merge_node(session, node)

    query, params = session.queries[0]
    assert params["props"]["filename"] == "Sample Work Order.pdf"
    assert "n.sources" not in query


# --- Bug 3: undirected Cypher leaked unrelated documents (commit 8658b59) --


def test_graph_document_scope_hop1_is_directed_mentions_only(client, install_fake_driver):
    """The first version of GET /graph?document=... used an undirected
    *1..3 hop match rooted at the Document node, which could walk backward
    through a shared entity into a DIFFERENT document's own MENTIONS edge
    (DocA -MENTIONS-> Equipment <-MENTIONS- DocB) - leaking unrelated
    documents (4 extra Document nodes, 60%+ of the whole graph in the bug
    report) into what should have been a single-document view. The fix
    bounds hop 1 to a directed (d)-[:MENTIONS]->(hop1) match and excludes
    MENTIONS from the hop 2-3 relationship types entirely, since MENTIONS is
    the only relationship type that ever touches a Document node - making it
    structurally impossible for the traversal to reach any other Document.
    """
    driver = install_fake_driver([FakeResult(rows=[]), FakeResult(rows=[])])

    response = client.get("/graph", params={"document": "Sample Work Order.pdf"})

    assert response.status_code == 200
    node_query, node_params = driver.last_session.queries[0]
    assert node_params["document"] == "Sample Work Order.pdf"

    assert "(d)-[:MENTIONS]->(hop1)" in node_query
    # MENTIONS must appear exactly once - only in hop 1's directed clause -
    # never in the hop 2-3 traversal, and never as an undirected pattern.
    assert node_query.count("MENTIONS") == 1
    assert "-[:MENTIONS]-(hop1)" not in node_query  # would be undirected
    assert "*1..3" not in node_query  # the original bug's unbounded hop range


# --- Bug 5: LLM citation mismatch on dense tabular context (commit f17c954) -


def test_chat_citation_points_to_value_bearing_chunk_not_mention_only_chunk(
    client, mock_search_chunks, mock_lookup_equipment_fact, mock_groq_chat
):
    """On dense tabular multi-row context, the fix chain in f17c954 ended
    with the model correctly stating the literal value but citing whichever
    chunk scored highest on retrieval (a prose mention of the equipment)
    instead of the chunk that actually contained the stated value - fixed by
    a prompt instruction requiring the citation to point at the exact
    excerpt containing the literal value. Actual model behavior can't be
    unit-tested, but the concrete, testable part of the fix is that the
    citation-extraction pipeline correctly threads through *whichever* chunk
    the model cites, all the way to the API response, without collapsing or
    defaulting to the highest-scored chunk when two retrieved chunks
    reference the same equipment tag.
    """
    mention_chunk = RetrievedChunk(
        text="Pump P-102 is a centrifugal pump installed in Building 3.",
        source_filename="Sample Work Order.pdf",
        page_number=1,
        chunk_id="Sample Work Order_p1_0",
        score=0.95,  # higher retrieval score than the value-bearing chunk
    )
    value_chunk = RetrievedChunk(
        text="P-102 | 2026-06-01 | J. Rao | None | Replaced seal | 2026-09-01",
        source_filename="Sample Work Order.pdf",
        page_number=4,
        chunk_id="Sample Work Order_p4_2",
        score=0.80,
    )
    mock_search_chunks.return_value = [mention_chunk, value_chunk]
    mock_groq_chat.return_value = (
        "P-102 is next due for inspection on 2026-09-01 [Sample Work Order.pdf, page 4]."
    )

    response = client.post("/chat", json={"question": "When is P-102 due for inspection?"})

    assert response.status_code == 200
    citations = response.json()["citations"]
    assert citations == [{"source_filename": "Sample Work Order.pdf", "page_number": 4}]
    # Specifically not the higher-scored, mention-only chunk's page - this is
    # exactly the mismatch the fix targeted.
    assert {"source_filename": "Sample Work Order.pdf", "page_number": 1} not in citations
