"""Generates backend/sample_docs/Sample Gauge Reading Photo.png - a standalone
image fixture (not wrapped in a PDF) for testing app.ingestion.image_loader.
ImageLoader, the new direct-to-OCR.space path for raw .png/.jpg/.jpeg
uploads (distinct from PDFLoader's per-page OCR fallback).

Draws realistic industrial content onto a blank image with PIL, mimicking a
field technician's photo of a handwritten/printed gauge reading log.

Deliberately distinct from every tag/date/status already used elsewhere in
the corpus, so a /chat answer citing this content can only have come from
this fixture.

Usage: .venv/bin/python scripts/generate_image_fixture.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BACKEND_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = BACKEND_DIR / "sample_docs" / "Sample Gauge Reading Photo.png"

LINES = [
    "GAUGE READING LOG",
    "",
    "Equipment Tag: PG-410",
    "Type: Pressure Gauge",
    "Location: Tank Farm Bay 3",
    "Reading Date: 2026-07-15",
    "Reading: 142 psig",
    "Technician: J. Whitfield",
    "Status: Within normal operating range",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def main() -> None:
    width, height = 1275, 1650
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    font_title = _load_font(48)
    font_body = _load_font(36)

    y = 100
    for i, line in enumerate(LINES):
        font = font_title if i == 0 else font_body
        draw.text((100, y), line, fill="black", font=font)
        y += 90 if i == 0 else 65

    image.save(OUTPUT_PATH)
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
