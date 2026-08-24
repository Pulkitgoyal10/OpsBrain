"""Standalone image loader (.png/.jpg/.jpeg), per CLAUDE.md B1.

Distinct from the OCR fallback PDFLoader uses internally for scanned PDF
pages (app.ingestion.ocr_loader.ocr_page): this is a real top-level upload
type now that OCR runs via the OCR.space cloud API, which accepts a raw
image directly - no PDF wrapping, no local rasterization step needed.

Non-paginated, like SpreadsheetLoader and DXFLoader: a standalone image is
one unit, not a multi-page document, so it collapses into a single PageText
with page_number=None and chunker.chunk_pages() windows it the same way.
"""

from __future__ import annotations

from pathlib import Path

from app.ingestion.base import DocumentLoader, PageText
from app.ingestion.ocr_loader import ocr_image_file


class ImageLoader(DocumentLoader):
    doc_type = "image"

    def extract(self, file_path: Path) -> list[PageText]:
        text = ocr_image_file(file_path).strip()
        if not text:
            return []
        return [PageText(page_number=None, text=text)]
