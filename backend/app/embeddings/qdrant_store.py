"""Qdrant vector store for chunk embeddings, per CLAUDE.md B2."""

from __future__ import annotations

import os
import uuid
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from app.embeddings.cohere_client import EMBED_DIM
from app.ingestion.base import Chunk

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

COLLECTION_NAME = "opsbrain_chunks"

_client: QdrantClient | None = None


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(
            url=os.environ["QDRANT_URL"],
            api_key=os.environ["QDRANT_API_KEY"],
        )
    return _client


def ensure_collection(client: QdrantClient, name: str = COLLECTION_NAME) -> None:
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )
    # Required for query_points' query_filter on source_filename (document
    # scoping - app.rag.retrieval.search_chunks): Qdrant only allows
    # filtering on indexed payload fields, and this collection predates
    # that filter. Called unconditionally (not just on fresh-collection
    # creation) so it also backfills the index onto an already-existing
    # collection; idempotent, safe to call every startup.
    client.create_payload_index(
        collection_name=name,
        field_name="source_filename",
        field_schema=PayloadSchemaType.KEYWORD,
    )


def upsert_chunks(
    client: QdrantClient,
    chunks: list[Chunk],
    vectors: list[list[float]],
    *,
    name: str = COLLECTION_NAME,
) -> None:
    """Upsert chunks with their vectors. Payload = ChunkMetadata fields + chunk text.

    Point IDs are deterministic (uuid5 of chunk_id), so re-ingesting the same
    chunk overwrites rather than duplicates it. chunk_id itself is kept in the
    payload since Qdrant point IDs must be an int or UUID, not an arbitrary string.
    """
    points = [
        PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.metadata.chunk_id)),
            vector=vector,
            payload={**asdict(chunk.metadata), "text": chunk.text},
        )
        for chunk, vector in zip(chunks, vectors)
    ]
    client.upsert(collection_name=name, points=points)


def _filename_filter(filename: str) -> Filter:
    return Filter(must=[FieldCondition(key="source_filename", match=MatchValue(value=filename))])


def count_chunks_by_filename(client: QdrantClient, filename: str, *, name: str = COLLECTION_NAME) -> int:
    """Used by DELETE /documents/{filename} to decide whether the document
    exists at all - a document can have chunks in Qdrant with no Document
    node in Neo4j (POST /upload's "partial" status, graph write failed but
    ingestion succeeded), so this is checked independently of the graph.
    """
    return client.count(collection_name=name, count_filter=_filename_filter(filename)).count


def delete_chunks_by_filename(
    client: QdrantClient, filename: str, *, name: str = COLLECTION_NAME
) -> None:
    client.delete(
        collection_name=name,
        points_selector=_filename_filter(filename),
        wait=True,
    )
