"""Picks the right DocumentLoader for a file, by extension.

Shared by scripts/run_ingestion_pipeline.py, scripts/run_graph_pipeline.py,
and POST /upload - extracted here once a third consumer needed the same
extension-to-loader dispatch (each script had its own inline copy before).
"""

from __future__ import annotations

from pathlib import Path

from app.ingestion.base import DocumentLoader
from app.ingestion.dxf_loader import DXFLoader
from app.ingestion.image_loader import ImageLoader
from app.ingestion.pdf_loader import PDFLoader
from app.ingestion.spreadsheet_loader import SpreadsheetLoader

PDF_SUFFIXES = frozenset({".pdf"})
SPREADSHEET_SUFFIXES = frozenset({".csv", ".xlsx"})
DXF_SUFFIXES = frozenset({".dxf"})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})
SUPPORTED_SUFFIXES = PDF_SUFFIXES | SPREADSHEET_SUFFIXES | DXF_SUFFIXES | IMAGE_SUFFIXES


def get_loader_for_file(path: str | Path) -> DocumentLoader:
    suffix = Path(path).suffix.lower()
    if suffix in SPREADSHEET_SUFFIXES:
        return SpreadsheetLoader()
    if suffix in PDF_SUFFIXES:
        return PDFLoader()
    if suffix in DXF_SUFFIXES:
        return DXFLoader()
    if suffix in IMAGE_SUFFIXES:
        return ImageLoader()
    raise ValueError(
        f"Unsupported file type: {suffix!r} (expected one of {sorted(SUPPORTED_SUFFIXES)})"
    )
