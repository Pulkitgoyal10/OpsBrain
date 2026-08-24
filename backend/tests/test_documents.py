"""GET /documents, DELETE /documents/{filename}."""

from __future__ import annotations

from tests.conftest import FakeResult


def test_list_documents_returns_shape(client, install_fake_driver):
    install_fake_driver(
        [
            FakeResult(
                rows=[
                    {
                        "filename": "Sample Work Order.pdf",
                        "doc_type": "uploaded",
                        "title": "Sample Work Order.pdf",
                        "ingested_at": "2026-07-11T12:00:00+00:00",
                    }
                ]
            )
        ]
    )

    response = client.get("/documents")

    assert response.status_code == 200
    body = response.json()
    assert body["documents"] == [
        {
            "filename": "Sample Work Order.pdf",
            "doc_type": "uploaded",
            "title": "Sample Work Order.pdf",
            "ingested_at": "2026-07-11T12:00:00+00:00",
        }
    ]


def test_delete_document_not_found_returns_404(
    client, mock_qdrant_client, mock_count_chunks_by_filename, install_fake_driver
):
    mock_count_chunks_by_filename.return_value = 0
    install_fake_driver([FakeResult(rows=[])])  # existence check finds no Document node

    response = client.delete("/documents/Nonexistent.pdf")

    assert response.status_code == 404


def test_delete_document_removes_chunks_and_graph_together(
    client,
    mock_qdrant_client,
    mock_count_chunks_by_filename,
    install_fake_driver,
    mock_delete_chunks_by_filename,
    mock_delete_document_graph,
):
    mock_count_chunks_by_filename.return_value = 4
    install_fake_driver([FakeResult(rows=[{"d": {}}])])  # existence check finds the Document node

    response = client.delete("/documents/Sample Work Order.pdf")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["chunks_deleted"] == 4
    assert body["graph_nodes_deleted"] == 2
    assert body["graph_nodes_updated"] == 1
    assert body["graph_relationships_deleted"] == 3

    # The whole point of DELETE /documents/{filename} (per app/main.py's own
    # docstring) is that it "surgically retracts" a document on both sides -
    # Qdrant chunks and Neo4j graph contributions - not just one of them.
    mock_delete_chunks_by_filename.assert_called_once_with(
        mock_qdrant_client, "Sample Work Order.pdf"
    )
    mock_delete_document_graph.assert_called_once()
    assert mock_delete_document_graph.call_args.args[1] == "Sample Work Order.pdf"
