"""Generates backend/sample_docs/Sample Scanned Valve Inspection Note.pdf - a
second, independent image-only PDF fixture for testing the OCR.space fallback
in app.ingestion.pdf_loader, distinct from
"Sample Scanned Equipment Note.pdf" (equipment tag HX-950).

Same generation approach as scripts/generate_ocr_fixture.py: draws realistic
industrial content onto a blank image with PIL, embeds it into a fresh PDF
page with no text layer via PyMuPDF - genuinely triggering PDFLoader's "no
extractable text" -> OCR fallback path.

Usage: .venv/bin/python scripts/generate_valve_ocr_fixture.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont

OUTPUT_PATH = BACKEND_DIR / "sample_docs" / "Sample Scanned Valve Inspection Note.pdf"

# Deliberately distinct from every tag/date/status already used elsewhere in
# the corpus (including HX-950 in the other scanned fixture), so a /chat
# answer citing this content can only have come from this fixture.
LINES = [
    "FIELD INSPECTION NOTE",
    "",
    "Equipment Tag: V-770",
    "Type: Pressure Relief Valve",
    "Location: North Yard Compressor Station",
    "Inspection Date: 2026-07-10",
    "Next Due Date: 2026-10-10",
    "Inspector: M. Delacroix",
    "Status: OK - set pressure verified",
    "Notes: Popped and reseated cleanly at 150 psig, no leakage after reseat.",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def main() -> None:
    width, height = 1275, 1650  # letter-ish at ~150dpi
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    font_title = _load_font(48)
    font_body = _load_font(36)

    y = 100
    for i, line in enumerate(LINES):
        font = font_title if i == 0 else font_body
        draw.text((100, y), line, fill="black", font=font)
        y += 90 if i == 0 else 65

    img_path = OUTPUT_PATH.with_suffix(".png")
    image.save(img_path)

    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    page.insert_image(fitz.Rect(0, 0, width, height), filename=str(img_path))
    doc.save(OUTPUT_PATH)
    doc.close()
    img_path.unlink()

    # Sanity check at generation time: confirm the page really has zero
    # extractable native text, i.e. this fixture actually exercises the
    # OCR fallback rather than accidentally having a text layer.
    check = fitz.open(OUTPUT_PATH)
    native_text = check[0].get_text().strip()
    check.close()
    assert not native_text, f"Fixture has extractable text, not image-only: {native_text!r}"

    print(f"Wrote {OUTPUT_PATH} (1 page, image-only, {len(native_text)} chars of native text)")


if __name__ == "__main__":
    main()
