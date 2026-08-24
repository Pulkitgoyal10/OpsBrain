"""Shared retrieval: embed a query and search Qdrant for matching chunks."""

from __future__ import annotations

from dataclasses import dataclass

from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from app.embeddings.cohere_client import embed_texts
from app.embeddings.qdrant_store import COLLECTION_NAME, get_client

DEFAULT_TOP_K = 5


@dataclass(frozen=True)
class RetrievedChunk:
    text: str
    source_filename: str
    page_number: int | None
    chunk_id: str
    score: float


def search_chunks(
    query: str, top_k: int = DEFAULT_TOP_K, document: str | None = None
) -> list[RetrievedChunk]:
    """document, when given, restricts the search to chunks whose
    source_filename payload field matches exactly - Qdrant payload filtering
    applied server-side (not a post-hoc Python filter), so top_k chunks are
    still drawn from the scoped set, not the whole collection.
    """
    [vector] = embed_texts([query], input_type="search_query")
    client = get_client()
    query_filter = (
        Filter(must=[FieldCondition(key="source_filename", match=MatchValue(value=document))])
        if document is not None
        else None
    )
    response = client.query_points(
        collection_name=COLLECTION_NAME,
        query=vector,
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    )
    return [
        RetrievedChunk(
            text=point.payload["text"],
            source_filename=point.payload["source_filename"],
            page_number=point.payload["page_number"],
            chunk_id=point.payload["chunk_id"],
            score=point.score,
        )
        for point in response.points
    ]
