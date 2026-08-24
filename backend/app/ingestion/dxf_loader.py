"""DXF (CAD drawing) loader, per CLAUDE.md B1.

DXF is a plain-text/binary vector-drawing format (AutoCAD's open exchange
format) - there's no "page" concept, so like SpreadsheetLoader this collapses
a whole file into a single PageText with page_number=None and lets
chunker.chunk_pages() window it.

Pulls exactly the entity types that carry human-readable content on a real
engineering drawing: title-block fields (INSERT block references with
attached ATTRIB values - e.g. drawing number, revision, date), free text
(TEXT/MTEXT annotations - e.g. an equipment tag written next to a symbol),
dimension annotations, and layer names (often meaningful on their own, e.g.
an "EQUIPMENT" or "PIPING" layer). Geometry itself (lines, circles, polylines)
carries no extractable text and is skipped - the whole point is entities
with an ASCII payload the LLM/embeddings pipeline can use.

Uses ezdxf (pure Python, pip-installable) - no compiled/system dependency,
unlike a real CAD engine. DWG (the proprietary Autodesk binary format) is
explicitly out of scope: ezdxf cannot read it and there is no equivalent
pure-Python DWG parser, so DWG files are not a supported upload type. See
README.md / TODO.md.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
from ezdxf.document import Drawing

from app.ingestion.base import DocumentLoader, PageText

_SKIP_LAYERS = frozenset({"0", "Defpoints"})


class DXFLoader(DocumentLoader):
    doc_type = "dxf"

    def extract(self, file_path: Path) -> list[PageText]:
        doc = ezdxf.readfile(file_path)
        lines: list[str] = []

        layer_names = sorted(
            layer.dxf.name for layer in doc.layers if layer.dxf.name not in _SKIP_LAYERS
        )
        if layer_names:
            lines.append("Layers: " + ", ".join(layer_names))

        for layout in _layouts(doc):
            for entity in layout:
                lines.extend(_entity_lines(entity))

        text = "\n".join(lines)
        if not text:
            return []
        return [PageText(page_number=None, text=text)]


def _layouts(doc: Drawing):
    yield doc.modelspace()
    for name in doc.layout_names():
        if name != "Model":
            yield doc.layout(name)


def _entity_lines(entity) -> list[str]:
    dxftype = entity.dxftype()

    if dxftype == "TEXT":
        text = entity.dxf.text.strip()
        return [text] if text else []

    if dxftype == "MTEXT":
        text = entity.plain_text().strip()
        return [text] if text else []

    if dxftype == "DIMENSION":
        return _dimension_lines(entity)

    if dxftype == "INSERT":
        return _insert_lines(entity)

    return []


def _dimension_lines(entity) -> list[str]:
    override = (entity.dxf.text or "").strip()
    if override and override != "<>":
        return [f"Dimension: {override}"]
    try:
        measurement = entity.get_measurement()
    except Exception:
        return []
    if not measurement:
        return []
    return [f"Dimension: {measurement:.3g}"]


def _insert_lines(entity) -> list[str]:
    pairs = [
        f"{attrib.dxf.tag}: {attrib.dxf.text.strip()}"
        for attrib in entity.attribs
        if attrib.dxf.text.strip()
    ]
    if not pairs:
        return []
    return [f"{entity.dxf.name} — " + ", ".join(pairs)]
