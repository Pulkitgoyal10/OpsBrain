"""Generates backend/sample_docs/Sample Equipment Drawing.dxf - a small,
synthetic CAD drawing with realistic labeled content, for testing
app.ingestion.dxf_loader.DXFLoader.

Builds a title block (as a block reference with filled-in ATTRIB values -
the real-world mechanism CAD title blocks use), an equipment tag as a TEXT
entity, an MTEXT note, a dimension, and a few named layers - exactly the
entity types DXFLoader.extract() knows how to read.

Deliberately distinct from every tag/date/status already used elsewhere in
the corpus, so a /chat answer citing this content can only have come from
this fixture, not from ambiguity with an existing document.

Usage: .venv/bin/python scripts/generate_dxf_fixture.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import ezdxf

OUTPUT_PATH = BACKEND_DIR / "sample_docs" / "Sample Equipment Drawing.dxf"

TITLE_BLOCK_FIELDS = {
    "DWG_NO": "DWG-7734",
    "REVISION": "B",
    "DATE": "2026-06-02",
    "DRAWN_BY": "R. Okafor",
}


def main() -> None:
    doc = ezdxf.new("R2010")

    for layer_name in ("EQUIPMENT", "ANNOTATION", "DIMENSIONS", "TITLEBLOCK"):
        doc.layers.add(layer_name)

    msp = doc.modelspace()

    title_block = doc.blocks.new(name="TITLEBLOCK")
    title_block.add_line((0, 0), (200, 0))
    for i, (tag, value) in enumerate(TITLE_BLOCK_FIELDS.items()):
        title_block.add_attdef(tag, text=value, dxfattribs={"insert": (5, 5 + i * 8)})

    insert = msp.add_blockref("TITLEBLOCK", (0, 0), dxfattribs={"layer": "TITLEBLOCK"})
    insert.add_auto_attribs(TITLE_BLOCK_FIELDS)

    msp.add_text(
        "COMPRESSOR C-310",
        dxfattribs={"layer": "EQUIPMENT", "height": 5},
    ).set_placement((0, 60))

    msp.add_mtext(
        "Skid-mounted reciprocating compressor.\nRated discharge pressure: 250 psig.",
        dxfattribs={"layer": "ANNOTATION"},
    ).set_location((0, 80))

    dim = msp.add_linear_dim(
        base=(0, -10),
        p1=(0, 0),
        p2=(180, 0),
        dxfattribs={"layer": "DIMENSIONS"},
    )
    dim.render()

    doc.saveas(OUTPUT_PATH)

    # Sanity check at generation time: confirm the file round-trips through
    # DXFLoader and actually yields extractable text, not an empty drawing.
    from app.ingestion.dxf_loader import DXFLoader

    pages = DXFLoader().extract(OUTPUT_PATH)
    assert pages, "Fixture produced no extractable text"
    text = pages[0].text
    for expected in ("DWG-7734", "COMPRESSOR C-310", "250 psig"):
        assert expected in text, f"Fixture missing expected content: {expected!r}"

    print(f"Wrote {OUTPUT_PATH}\n---\n{text}")


if __name__ == "__main__":
    main()
