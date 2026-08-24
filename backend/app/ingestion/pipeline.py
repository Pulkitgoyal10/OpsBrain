"""Orchestrates embedding ingestion for one document: load -> embed -> upsert.

Shared by scripts/run_ingestion_pipeline.py and the POST /upload endpoint.
"""

from __future__ import annotations

from pathlib import Path

from app.embeddings.cohere_client import embed_texts
from app.embeddings.qdrant_store import get_client, upsert_chunks
from app.ingestion.base import Chunk
from app.ingestion.pdf_loader import PDFLoader


def ingest_pdf(loader: PDFLoader, path: Path) -> list[Chunk]:
    """Load, chunk, embed, and upsert one PDF. Returns the chunks (for
    callers that need counts/content, e.g. the /upload response summary).
    """
    chunks = loader.load(path)
    if not chunks:
        return chunks

    vectors = embed_texts([c.text for c in chunks])
    upsert_chunks(get_client(), chunks, vectors)
    return chunks
