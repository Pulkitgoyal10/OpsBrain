"""POST /upload."""

from __future__ import annotations


def test_upload_rejects_unsupported_file_type(client):
    response = client.post(
        "/upload", files={"file": ("notes.txt", b"plain text content", "text/plain")}
    )

    assert response.status_code == 415
    assert ".txt" in response.json()["detail"]


def test_upload_success_triggers_qdrant_and_graph_writes(
    client,
    mock_loader,
    mock_embed_texts,
    mock_qdrant_client,
    mock_upsert_chunks,
    mock_build_graph_from_document,
    mock_write_graph,
):
    response = client.post(
        "/upload", files={"file": ("Sample Work Order.pdf", b"%PDF-1.4 fake bytes", "application/pdf")}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["filename"] == "Sample Work Order.pdf"
    assert body["chunks_created"] == 1
    assert body["graph_nodes_created"] == 1
    assert body["graph_relationships_created"] == 0
    assert body["warnings"] == []

    # The Qdrant vector store must actually receive the ingested chunks -
    # this is the Cohere-embed -> Qdrant-upsert half of /upload's contract.
    mock_upsert_chunks.assert_called_once()
    _, upserted_chunks, upserted_vectors = mock_upsert_chunks.call_args.args
    assert len(upserted_chunks) == 1
    assert len(upserted_vectors) == 1

    # The Neo4j graph must actually receive the extracted nodes/edges - the
    # extraction -> Neo4j-write half of /upload's contract.
    mock_write_graph.assert_called_once()
    written_nodes, written_rels = mock_write_graph.call_args.args
    assert len(written_nodes) == 1
    assert written_rels == []
