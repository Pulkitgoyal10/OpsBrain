"""PDF loader: PyMuPDF for native text, pdfplumber for tables, per CLAUDE.md B1."""

from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber

from app.ingestion.base import DocumentLoader, PageText
from app.ingestion.ocr_loader import OCRUnavailableError, ocr_page

logger = logging.getLogger(__name__)

_BLANK_LINES = re.compile(r"\n{3,}")

# How many lines from the top/bottom of a page count as "edge" lines when
# looking for repeated headers/footers, and how much of the document a line
# must appear in (as an edge line) to be treated as boilerplate.
_EDGE_LINES = 3
_BOILERPLATE_THRESHOLD = 0.6
_MIN_PAGES_FOR_BOILERPLATE_DETECTION = 3


class PDFLoader(DocumentLoader):
    doc_type = "pdf"

    def extract(self, file_path: Path) -> list[PageText]:
        pages: list[PageText] = []

        with fitz.open(file_path) as doc, pdfplumber.open(file_path) as plumber_doc:
            natural_texts: list[str] = []
            page_tables: list[list[list[list[str | None]]]] = []

            for page_index, page in enumerate(doc, start=1):
                # find_tables() (not extract_tables()) so we get each table's
                # bbox too - needed to exclude its cells from the natural-order
                # text below and avoid extracting the same table content twice.
                tables = plumber_doc.pages[page_index - 1].find_tables()
                table_bboxes = [fitz.Rect(t.bbox) for t in tables]

                natural_texts.append(
                    _extract_text_excluding_tables(page, table_bboxes)
                    if table_bboxes
                    else page.get_text()
                )
                page_tables.append([t.extract() for t in tables])

            boilerplate = _detect_boilerplate_lines(natural_texts)

            for page_index, natural_text in enumerate(natural_texts, start=1):
                text = _strip_boilerplate(natural_text, boilerplate).strip()

                tables = page_tables[page_index - 1]
                if tables:
                    text += "\n\n" + "\n\n".join(_table_to_text(t) for t in tables)

                text = _normalize(text)

                if not text:
                    # Page has no extractable text - likely a scanned image
                    # (or a pure diagram/photo). Fall back to OCR on just
                    # this page rather than dropping its content outright.
                    try:
                        ocr_text = _normalize(ocr_page(doc[page_index - 1]))
                    except OCRUnavailableError:
                        logger.warning(
                            "page %d of %s has no extractable text (likely scanned) "
                            "and OCR is unavailable in this environment; skipping",
                            page_index,
                            file_path.name,
                        )
                        continue

                    if not ocr_text:
                        logger.warning(
                            "page %d of %s has no extractable text (likely scanned) "
                            "and OCR found none either; skipping",
                            page_index,
                            file_path.name,
                        )
                        continue

                    logger.warning(
                        "page %d of %s has no extractable text (likely scanned); "
                        "used OCR instead",
                        page_index,
                        file_path.name,
                    )
                    pages.append(PageText(page_number=page_index, text=ocr_text))
                    continue

                pages.append(PageText(page_number=page_index, text=text))

        return pages

    def extract_tables(
        self, file_path: str | Path
    ) -> list[tuple[int, list[list[str | None]]]]:
        """Return (page_number, rows) for every table detected in the PDF.

        Used by the graph extractor's deterministic table path so structured
        rows (e.g. the maintenance inspection table) map straight to nodes -
        no LLM, which is exactly the tabular case an LLM handles worst.
        """
        results: list[tuple[int, list[list[str | None]]]] = []
        with pdfplumber.open(file_path) as plumber_doc:
            for page_index, page in enumerate(plumber_doc.pages, start=1):
                for table in page.extract_tables():
                    results.append((page_index, table))
        return results


def _extract_text_excluding_tables(page: fitz.Page, table_bboxes: list[fitz.Rect]) -> str:
    """Natural reading-order text, minus any word inside a detected table.

    Without this, a page's text contains every table cell twice: once from
    PyMuPDF's word-by-word extraction and again from the clean pipe-delimited
    version built in `extract()` - which roughly doubles token usage on
    table-heavy pages and can leave a truncated row fragment right at a chunk
    boundary. pdfplumber and PyMuPDF page coordinates both use a top-left
    origin, so bboxes compare directly with no axis flip needed.
    """
    lines: dict[tuple[int, int], list[str]] = {}
    order: list[tuple[int, int]] = []

    for x0, y0, x1, y1, word, block_no, line_no, _word_no in page.get_text("words"):
        center = fitz.Point((x0 + x1) / 2, (y0 + y1) / 2)
        if any(center in rect for rect in table_bboxes):
            continue
        key = (block_no, line_no)
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append(word)

    return "\n".join(" ".join(lines[key]) for key in order)


def _detect_boilerplate_lines(raw_texts: list[str]) -> set[str]:
    """Find lines that repeat, verbatim, near the top/bottom of most pages.

    Exact-match only - a footer like "Page 3 / 10" that changes per page
    won't be caught, only truly identical lines (e.g. document title,
    revision date) repeated across pages.
    """
    if len(raw_texts) < _MIN_PAGES_FOR_BOILERPLATE_DETECTION:
        return set()

    counts: Counter[str] = Counter()
    for text in raw_texts:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        edge_lines = set(lines[:_EDGE_LINES]) | set(lines[-_EDGE_LINES:])
        counts.update(edge_lines)

    threshold = len(raw_texts) * _BOILERPLATE_THRESHOLD
    return {line for line, count in counts.items() if count >= threshold}


def _strip_boilerplate(text: str, boilerplate: set[str]) -> str:
    if not boilerplate:
        return text
    lines = [line for line in text.splitlines() if line.strip() not in boilerplate]
    return "\n".join(lines)


def _table_to_text(table: list[list[str | None]]) -> str:
    rows = [" | ".join(cell or "" for cell in row) for row in table]
    return "\n".join(rows)


def _normalize(text: str) -> str:
    # TODO: page-numbered footers (e.g. "Page 3 / 10") aren't caught by
    # _detect_boilerplate_lines since the text differs per page - revisit
    # with a pattern-based approach once we see more sample docs.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()
