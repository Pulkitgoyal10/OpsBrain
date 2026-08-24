"""Cohere embed-english-v3.0 wrapper, per CLAUDE.md B2."""

from __future__ import annotations

import os
import time
from pathlib import Path

import cohere
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

MODEL = "embed-english-v3.0"
EMBED_DIM = 1024

# Cohere's embed endpoint caps a single call at 96 texts.
_BATCH_SIZE = 96
_BATCH_DELAY_SECONDS = 1.0

_client: cohere.ClientV2 | None = None


def _get_client() -> cohere.ClientV2:
    global _client
    if _client is None:
        api_key = os.environ["COHERE_API_KEY"]
        _client = cohere.ClientV2(api_key=api_key)
    return _client


def embed_texts(
    texts: list[str],
    *,
    input_type: str = "search_document",
) -> list[list[float]]:
    """Embed a list of chunk texts, batching to respect Cohere's per-call limit."""
    client = _get_client()
    vectors: list[list[float]] = []

    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i : i + _BATCH_SIZE]
        response = client.embed(
            model=MODEL,
            input_type=input_type,
            texts=batch,
            embedding_types=["float"],
        )
        vectors.extend(response.embeddings.float_)

        if i + _BATCH_SIZE < len(texts):
            time.sleep(_BATCH_DELAY_SECONDS)

    return vectors
