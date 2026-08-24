"""Shared fixtures for the backend test suite.

No test in this suite makes a real network call. Every external service
(Cohere, Qdrant, Neo4j, Groq) is mocked at its point of use via monkeypatch -
each fixture patches both the function's defining module AND every module
that imported it directly with `from x import y` (a plain patch of the
defining module does nothing for a caller that already bound its own local
name to the original function object).

Two designs worth calling out:

- `client`: replaces the FastAPI app's lifespan with a no-op before creating
  the TestClient, so app startup never calls the real ensure_collection()/
  ensure_constraints() against real Qdrant/Neo4j.

- `FakeLoader`: a real app.ingestion.base.DocumentLoader subclass used in
  place of PDFLoader/etc. for upload tests. It overrides `load()` directly
  instead of relying on the base class's real implementation, which calls
  app.ingestion.chunker.chunk_pages() -> tiktoken.get_encoding("cl100k_base").
  That encoding is lazily fetched by tiktoken from a remote blob store on
  first use and only cached locally after - fine on a dev machine that's
  already warmed the cache, but not something a test suite should depend on
  (a fresh clone or CI box won't have it cached), so real chunking is never
  exercised here.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.graph.neo4j_store import GraphDeletionSummary
from app.ingestion.base import Chunk, ChunkMetadata, DocumentLoader, PageText
from app.main import app

# --- FastAPI TestClient with startup/shutdown neutralized ------------------


@asynccontextmanager
async def _noop_lifespan(_app):
    yield


@pytest.fixture
def client():
    app.router.lifespan_context = _noop_lifespan
    with TestClient(app) as c:
        yield c


# --- Fake Neo4j driver/session ----------------------------------------------
#
# app.main issues raw Cypher directly for a few read endpoints (/documents,
# /graph, /suggested-questions) instead of going through a helper function,
# so there's no single function to monkeypatch for those - the driver itself
# has to be faked. FakeSession hands back one queued FakeResult per .run()
# call, in the order the endpoint under test is known to call it (verified
# by reading app/main.py), and records every (query, params) pair so a test
# can assert on the Cypher shape itself (used by the bug-3 regression test).


class FakeResult:
    def __init__(self, rows: list[dict] | None = None):
        self._rows = rows or []

    def data(self) -> list[dict]:
        return list(self._rows)

    def single(self) -> dict | None:
        return self._rows[0] if self._rows else None


class FakeSession:
    def __init__(self, queue: list[FakeResult]):
        self._queue = list(queue)
        self.queries: list[tuple[str, dict]] = []

    def run(self, query: str, **params) -> FakeResult:
        self.queries.append((query, params))
        if not self._queue:
            raise AssertionError(
                f"FakeSession.run() called more times than results were queued.\nQuery: {query}"
            )
        return self._queue.pop(0)

    def execute_write(self, fn, *args, **kwargs):
        return fn(self, *args, **kwargs)

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *exc) -> bool:
        return False


class FakeDriver:
    def __init__(self, queue: list[FakeResult]):
        self._queue = queue
        self.last_session: FakeSession | None = None

    def session(self) -> FakeSession:
        self.last_session = FakeSession(self._queue)
        return self.last_session


@pytest.fixture
def install_fake_driver(monkeypatch):
    """Factory: install_fake_driver([FakeResult(...), ...]) -> FakeDriver.

    Patches app.main.get_driver only - the endpoints that read via raw Cypher
    (/documents, /graph, /suggested-questions, and DELETE /documents'
    existence check) all call get_driver() through that one import site.
    """

    def _install(results: list[FakeResult]) -> FakeDriver:
        driver = FakeDriver(results)
        monkeypatch.setattr("app.main.get_driver", lambda: driver)
        return driver

    return _install


# --- Cohere ------------------------------------------------------------------


@pytest.fixture
def mock_embed_texts(monkeypatch):
    def _fake(texts, **kwargs):
        return [[0.01] * 1024 for _ in texts]

    fake = MagicMock(side_effect=_fake)
    monkeypatch.setattr("app.embeddings.cohere_client.embed_texts", fake)
    monkeypatch.setattr("app.rag.retrieval.embed_texts", fake)
    monkeypatch.setattr("app.ingestion.pipeline.embed_texts", fake)
    return fake


# --- Qdrant --------------------------------------------------------------


@pytest.fixture
def mock_qdrant_client(monkeypatch):
    fake_client = MagicMock(name="qdrant_client")
    monkeypatch.setattr("app.embeddings.qdrant_store.get_client", lambda: fake_client)
    monkeypatch.setattr("app.rag.retrieval.get_client", lambda: fake_client)
    monkeypatch.setattr("app.ingestion.pipeline.get_client", lambda: fake_client)
    monkeypatch.setattr("app.main.get_qdrant_client", lambda: fake_client)
    return fake_client


@pytest.fixture
def mock_upsert_chunks(monkeypatch):
    fake = MagicMock(return_value=None)
    monkeypatch.setattr("app.embeddings.qdrant_store.upsert_chunks", fake)
    monkeypatch.setattr("app.ingestion.pipeline.upsert_chunks", fake)
    return fake


@pytest.fixture
def mock_count_chunks_by_filename(monkeypatch):
    fake = MagicMock(return_value=3)
    monkeypatch.setattr("app.embeddings.qdrant_store.count_chunks_by_filename", fake)
    monkeypatch.setattr("app.main.count_chunks_by_filename", fake)
    return fake


@pytest.fixture
def mock_delete_chunks_by_filename(monkeypatch):
    fake = MagicMock(return_value=None)
    monkeypatch.setattr("app.embeddings.qdrant_store.delete_chunks_by_filename", fake)
    monkeypatch.setattr("app.main.delete_chunks_by_filename", fake)
    return fake


# --- Neo4j writes ------------------------------------------------------------


@pytest.fixture
def mock_write_graph(monkeypatch):
    fake = MagicMock(return_value=None)
    monkeypatch.setattr("app.graph.neo4j_store.write_graph", fake)
    monkeypatch.setattr("app.main.write_graph", fake)
    return fake


@pytest.fixture
def mock_delete_document_graph(monkeypatch):
    fake = MagicMock(
        return_value=GraphDeletionSummary(
            document_found=True, nodes_deleted=2, nodes_updated=1, relationships_deleted=3
        )
    )
    monkeypatch.setattr("app.graph.neo4j_store.delete_document_graph", fake)
    monkeypatch.setattr("app.main.delete_document_graph", fake)
    return fake


# --- Graph pipeline / graph lookup / compliance -----------------------------


@pytest.fixture
def mock_build_graph_from_document(monkeypatch):
    from app.graph import schema
    from app.graph.schema import GraphNode

    def _default(loader, path, *, doc_type, title):
        node = GraphNode(
            schema.DOCUMENT,
            {
                "filename": path.name,
                "doc_type": doc_type,
                "title": title,
                "ingested_at": "2026-01-01T00:00:00+00:00",
            },
        )
        return [node], []

    fake = MagicMock(side_effect=_default)
    monkeypatch.setattr("app.main.build_graph_from_document", fake)
    return fake


@pytest.fixture
def mock_lookup_equipment_fact(monkeypatch):
    fake = MagicMock(return_value=None)
    monkeypatch.setattr("app.rag.graph_lookup.lookup_equipment_fact", fake)
    monkeypatch.setattr("app.rag.answer.lookup_equipment_fact", fake)
    return fake


@pytest.fixture
def mock_search_chunks(monkeypatch):
    fake = MagicMock(return_value=[])
    monkeypatch.setattr("app.rag.retrieval.search_chunks", fake)
    monkeypatch.setattr("app.rag.answer.search_chunks", fake)
    return fake


@pytest.fixture
def mock_groq_chat(monkeypatch):
    fake = MagicMock(return_value="")
    monkeypatch.setattr("app.rag.groq_client.chat", fake)
    monkeypatch.setattr("app.rag.answer.chat", fake)
    monkeypatch.setattr("app.graph.extractor.chat", fake)
    return fake


@pytest.fixture
def mock_find_overdue_maintenance(monkeypatch):
    fake = MagicMock(return_value=[])
    monkeypatch.setattr("app.compliance.agent.find_overdue_maintenance", fake)
    monkeypatch.setattr("app.main.find_overdue_maintenance", fake)
    return fake


# --- Fake document loader (bypasses real PDF parsing + tiktoken) -----------


class FakeLoader(DocumentLoader):
    doc_type = "uploaded"

    def extract(self, file_path: Path) -> list[PageText]:
        return []

    def extract_tables(self, file_path):
        return []

    def load(self, file_path: str | Path) -> list[Chunk]:
        path = Path(file_path)
        return [
            Chunk(
                text="Fake extracted content for testing.",
                metadata=ChunkMetadata(
                    source_filename=path.name,
                    page_number=1,
                    doc_type=self.doc_type,
                    chunk_id=f"{path.stem}_p1_0",
                ),
            )
        ]


@pytest.fixture
def mock_loader(monkeypatch):
    loader = FakeLoader()
    monkeypatch.setattr("app.main.get_loader_for_file", lambda path: loader)
    return loader
