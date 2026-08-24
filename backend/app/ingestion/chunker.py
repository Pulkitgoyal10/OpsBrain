"""Shared token-based chunking used by every loader, per CLAUDE.md B1:
~500-800 token chunks with ~100 token overlap.
"""

from __future__ import annotations

from pathlib import Path

import tiktoken

from app.ingestion.base import Chunk, ChunkMetadata, PageText

_ENCODING = tiktoken.get_encoding("cl100k_base")

# cl100k_base is a close approximation, not Cohere embed-english-v3.0's actual
# tokenizer - don't treat these counts as precise once embeddings are wired up.
CHUNK_TOKENS = 650
OVERLAP_TOKENS = 100


def chunk_pages(
    pages: list[PageText],
    *,
    source_filename: str,
    doc_type: str,
    chunk_tokens: int = CHUNK_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[Chunk]:
    stem = Path(source_filename).stem
    stride = chunk_tokens - overlap_tokens
    chunks: list[Chunk] = []

    for page in pages:
        tokens = _ENCODING.encode(page.text)
        if not tokens:
            continue

        index = 0
        start = 0
        while start < len(tokens):
            window = tokens[start : start + chunk_tokens]
            text = _ENCODING.decode(window)
            chunk_id = f"{stem}_p{page.page_number}_{index}"
            chunks.append(
                Chunk(
                    text=text,
                    metadata=ChunkMetadata(
                        source_filename=source_filename,
                        page_number=page.page_number,
                        doc_type=doc_type,
                        chunk_id=chunk_id,
                    ),
                )
            )
            index += 1
            start += stride

    return chunks
