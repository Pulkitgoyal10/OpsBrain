"""Common interface all document loaders implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChunkMetadata:
    source_filename: str
    page_number: int | None
    doc_type: str
    chunk_id: str


@dataclass(frozen=True)
class Chunk:
    text: str
    metadata: ChunkMetadata


@dataclass(frozen=True)
class PageText:
    """Raw text extracted from one page/sheet, before chunking."""

    page_number: int | None
    text: str


class DocumentLoader(ABC):
    """Base class every document loader (PDF, spreadsheet, ...) implements.
    OCR is not a loader in its own right - it's a page-level fallback
    PDFLoader calls internally (see app.ingestion.ocr_loader), since a
    scanned page arrives as part of an otherwise-normal PDF, not a
    distinct file type.

    Subclasses only implement `extract`, which turns a file into raw
    per-page text. `load` is the public entrypoint callers use — it
    chunks that text consistently via `chunker.chunk_pages`.
    """

    doc_type: str

    @abstractmethod
    def extract(self, file_path: Path) -> list[PageText]:
        """Pull raw text out of file_path, one entry per page/sheet."""
        raise NotImplementedError

    def extract_tables(
        self, file_path: str | Path
    ) -> list[tuple[int | None, list[list[str | None]]]]:
        """Return (page_number, rows) for every table-shaped region in the
        file, for the graph extractor's deterministic table path
        (app.graph.extractor.extract_from_table). Default: no tables: a
        loader that doesn't have a natural table concept (or hasn't
        implemented one yet) just contributes nothing here, and its content
        goes through the LLM prose path instead. PDFLoader and
        SpreadsheetLoader override this with real implementations;
        page_number is None for non-paginated formats.
        """
        return []

    def load(self, file_path: str | Path) -> list[Chunk]:
        from app.ingestion.chunker import chunk_pages

        path = Path(file_path)
        pages = self.extract(path)
        return chunk_pages(pages, source_filename=path.name, doc_type=self.doc_type)
