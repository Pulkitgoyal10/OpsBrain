"""OCR via the OCR.space cloud API (https://ocr.space/ocrapi), per CLAUDE.md B1.

Two callers, one shared HTTP call (_call_ocr_space):

- `ocr_page`: a page-level fallback PDFLoader.extract() calls when a PDF
  page's native text extraction comes back empty (the "likely scanned"
  case) - engineering drawings and scanned documents usually arrive as
  image-only pages inside an otherwise-normal PDF, not a separate file
  type, so this isn't a standalone loader.
- `ocr_image_file`: used by ImageLoader (app.ingestion.image_loader) for a
  standalone .png/.jpg/.jpeg upload - OCR.space accepts a raw image
  directly, so no PDF wrapping or rasterization step is needed there.

Uses OCR.space instead of a local Tesseract binary - Render's native Python
runtime has no OS-level package install, and this dev machine (macOS 12)
can't build a local Tesseract either (see TODO.md). A cloud API call is just
another outbound HTTPS request, identical in deployment shape to the
existing Cohere/Groq calls - no Docker switch, no system binary, works
unchanged in every environment.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import fitz  # PyMuPDF
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

logger = logging.getLogger(__name__)

_OCR_SPACE_URL = "https://api.ocr.space/parse/image"

# Tesseract's accuracy drops sharply below ~200-300 DPI on typical scanned
# document text; a PDF page's native resolution (72 DPI in PyMuPDF's default
# pixmap) is too low to OCR reliably. Same threshold applies to OCR.space's
# engine.
_OCR_DPI = 300

_REQUEST_TIMEOUT_SECONDS = 30

_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


class OCRUnavailableError(RuntimeError):
    """OCR_SPACE_API_KEY isn't configured in this environment - distinct
    from a genuine "OCR ran but found nothing" result or a transient API
    failure (network error, invalid key, rate limit, oversized file), so
    callers can tell "OCR isn't set up here at all" apart from "OCR was
    attempted and failed", though both degrade the same way today (skip the
    page / produce no chunks) rather than failing the whole upload.
    """


def _call_ocr_space(image_bytes: bytes, filename: str, content_type: str) -> str:
    """POST image bytes to OCR.space and return recognized text, stripped.

    Possibly empty if the image genuinely has no text (e.g. a pure diagram/
    photo with no writing) or if the call fails for any reason (network
    error, rate limit, oversized file, API-side error) - callers treat empty
    string as "nothing to add" either way. Raises OCRUnavailableError only
    when no API key is configured at all.
    """
    api_key = os.getenv("OCR_SPACE_API_KEY")
    if not api_key:
        raise OCRUnavailableError(
            "OCR_SPACE_API_KEY not set - OCR is unavailable in this environment"
        )

    try:
        response = requests.post(
            _OCR_SPACE_URL,
            files={"file": (filename, image_bytes, content_type)},
            data={"apikey": api_key, "language": "eng", "OCREngine": 2},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("OCR.space request failed: %s", e)
        return ""

    if payload.get("IsErroredOnProcessing"):
        logger.warning("OCR.space returned an error: %s", payload.get("ErrorMessage"))
        return ""

    parsed_results = payload.get("ParsedResults") or []
    if not parsed_results:
        return ""

    return (parsed_results[0].get("ParsedText") or "").strip()


def ocr_page(page: fitz.Page) -> str:
    """Rasterize one PDF page (PNG - lossless, no Pillow needed, and
    empirically *smaller* than JPEG for scanned text-on-white pages, which
    matters against OCR.space's free-tier 1 MB per-file cap) and OCR it.
    """
    pix = page.get_pixmap(dpi=_OCR_DPI)
    image_bytes = pix.tobytes("png")
    return _call_ocr_space(image_bytes, "page.png", "image/png")


def ocr_image_file(file_path: Path) -> str:
    """OCR a standalone image file (.png/.jpg/.jpeg) directly - no
    rasterization needed, OCR.space accepts the raw bytes as-is.
    """
    content_type = _CONTENT_TYPES.get(file_path.suffix.lower(), "image/png")
    return _call_ocr_space(file_path.read_bytes(), file_path.name, content_type)
