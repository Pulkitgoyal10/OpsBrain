"""GET /graph."""

from __future__ import annotations

from neo4j.exceptions import Neo4jError

from tests.conftest import FakeResult


def test_get_graph_returns_nodes_and_edges_shape(client, install_fake_driver):
    install_fake_driver(
        [
            FakeResult(rows=[{"eid": "n1", "label": "Equipment", "props": {"tag": "P-102"}}]),
            FakeResult(rows=[]),  # no edges among a single node
        ]
    )

    response = client.get("/graph")

    assert response.status_code == 200
    body = response.json()
    assert body["nodes"] == [
        {"id": "Equipment:P-102", "label": "Equipment", "properties": {"tag": "P-102"}}
    ]
    assert body["edges"] == []


def test_get_graph_scoped_to_document_uses_document_param(client, install_fake_driver):
    driver = install_fake_driver(
        [
            FakeResult(
                rows=[
                    {
                        "eid": "n1",
                        "label": "Document",
                        "props": {"filename": "Sample Work Order.pdf"},
                    }
                ]
            ),
            FakeResult(rows=[]),
        ]
    )

    response = client.get("/graph", params={"document": "Sample Work Order.pdf"})

    assert response.status_code == 200
    node_query, node_params = driver.last_session.queries[0]
    assert node_params["document"] == "Sample Work Order.pdf"


def test_get_graph_returns_500_on_neo4j_error(client, monkeypatch):
    def _raise():
        raise Neo4jError("connection refused")

    monkeypatch.setattr("app.main.get_driver", _raise)

    response = client.get("/graph")

    assert response.status_code == 500
