"""Spreadsheet/CSV loader, per CLAUDE.md B1.

Supports .csv and .xlsx. Non-paginated per the ChunkMetadata contract: every
row becomes one "Header: value | Header: value | ..." text line (mirroring
how PDFLoader renders a table row), and the whole file collapses into a
single PageText with page_number=None - chunker.chunk_pages() then windows
that text exactly as it already does for a PDF page, no special-casing
needed there.

A multi-sheet XLSX is flattened into that same single PageText (each row
prefixed with its sheet name when there's more than one) rather than one
PageText per sheet. chunker.py's chunk index resets to 0 for every PageText,
so two sheets both carrying page_number=None would otherwise produce
colliding chunk_ids (e.g. two different sheets' first chunk both landing on
"{stem}_pNone_0").
"""

from __future__ import annotations

import csv
from pathlib import Path

import openpyxl

from app.ingestion.base import DocumentLoader, PageText


class SpreadsheetLoader(DocumentLoader):
    doc_type = "spreadsheet"

    def extract(self, file_path: Path) -> list[PageText]:
        rows_by_sheet = _read_rows_by_sheet(file_path)
        text = _render(rows_by_sheet)
        if not text:
            return []
        return [PageText(page_number=None, text=text)]

    def extract_tables(
        self, file_path: str | Path
    ) -> list[tuple[int | None, list[list[str | None]]]]:
        """Each sheet's rows (incl. header) as one "table", page_number=None -
        reuses the same parsed rows as extract() so the graph extractor's
        deterministic table path (app.graph.extractor.extract_from_table)
        can parse a maintenance-shaped spreadsheet exactly like a PDF table,
        without going through the LLM prose path.
        """
        rows_by_sheet = _read_rows_by_sheet(Path(file_path))
        return [(None, rows) for rows in rows_by_sheet.values() if rows]


def _read_rows_by_sheet(file_path: Path) -> dict[str | None, list[list[str]]]:
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        return {None: _read_csv(file_path)}
    if suffix == ".xlsx":
        return _read_xlsx(file_path)
    raise ValueError(f"Unsupported spreadsheet format: {suffix!r} (expected .csv or .xlsx)")


def _read_csv(file_path: Path) -> list[list[str]]:
    with open(file_path, newline="", encoding="utf-8") as f:
        return [row for row in csv.reader(f)]


def _read_xlsx(file_path: Path) -> dict[str, list[list[str]]]:
    workbook = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    try:
        return {
            sheet.title: [
                ["" if cell is None else str(cell) for cell in row]
                for row in sheet.iter_rows(values_only=True)
            ]
            for sheet in workbook.worksheets
        }
    finally:
        workbook.close()


def _render(rows_by_sheet: dict[str | None, list[list[str]]]) -> str:
    prefix_sheet = len(rows_by_sheet) > 1
    lines: list[str] = []

    for sheet_name, rows in rows_by_sheet.items():
        if not rows:
            continue
        header, *data_rows = rows
        for row in data_rows:
            if not any(cell.strip() for cell in row):
                continue
            pairs = [f"{h.strip()}: {c.strip()}" for h, c in zip(header, row) if h.strip()]
            if not pairs:
                continue
            line = " | ".join(pairs)
            if prefix_sheet and sheet_name:
                line = f"[{sheet_name}] {line}"
            lines.append(line)

    return "\n".join(lines)
