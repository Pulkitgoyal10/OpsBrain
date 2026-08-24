"""Ad-hoc retrieval smoke test: embed a query and search Qdrant for top matches.

Usage: .venv/bin/python scripts/test_retrieval.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.rag.retrieval import DEFAULT_TOP_K, search_chunks  # noqa: E402

TEST_QUERIES = [
    "What PPE is required when handling sodium hypochlorite?",
    "When is pump P-102 due for its next inspection?",
]


def main() -> None:
    for query in TEST_QUERIES:
        print("=" * 80)
        print(f"QUERY: {query}")
        print("=" * 80)

        for rank, chunk in enumerate(search_chunks(query, top_k=DEFAULT_TOP_K), start=1):
            print(
                f"[{rank}] score={chunk.score:.4f}  "
                f"{chunk.source_filename}  page {chunk.page_number}  "
                f"({chunk.chunk_id})"
            )
            print(chunk.text)
            print("-" * 80)

        print()


if __name__ == "__main__":
    main()
