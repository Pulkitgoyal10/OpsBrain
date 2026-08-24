"""Load sample docs -> chunk -> embed via Cohere -> upsert into Qdrant.

Proves the Phase 3 pipeline end-to-end.
Usage: .venv/bin/python scripts/run_ingestion_pipeline.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.embeddings.qdrant_store import COLLECTION_NAME, ensure_collection, get_client  # noqa: E402
from app.ingestion.loader_registry import get_loader_for_file  # noqa: E402
from app.ingestion.pipeline import ingest_pdf  # noqa: E402

SAMPLE_DOCS = [
    BACKEND_DIR / "sample_docs" / "Sample SDS Handout.pdf",
    BACKEND_DIR / "sample_docs" / "Sample Maintenance Inspection Report.pdf",
    BACKEND_DIR / "sample_docs" / "Sample Work Order.pdf",
    BACKEND_DIR / "sample_docs" / "Sample Spreadsheet Inspection Log.xlsx",
]


def main() -> None:
    ensure_collection(get_client())

    summary: list[tuple[str, int]] = []

    for path in SAMPLE_DOCS:
        print(f"Loading {path.name} ...")
        chunks = ingest_pdf(get_loader_for_file(path), path)
        print(f"  {len(chunks)} chunks embedded and upserted")
        summary.append((path.name, len(chunks)))

    total_points = get_client().count(COLLECTION_NAME).count

    print("\n--- Summary ---")
    for filename, chunk_count in summary:
        print(f"  {filename}: {chunk_count} chunks embedded and stored")
    print(f"  Total chunks this run: {sum(c for _, c in summary)}")
    print(f"  Total points now in '{COLLECTION_NAME}': {total_points}")


if __name__ == "__main__":
    main()
