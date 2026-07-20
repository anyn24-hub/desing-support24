from __future__ import annotations

import io
from typing import Callable

import pypdf


def split_text_and_scanned(
    sources: list[tuple[str, bytes]],
) -> tuple[list[dict], list[tuple[str, bytes]]]:
    """
    Extract text from each page of every (filename, pdf_bytes) pair.

    Returns a tuple of:
        - pages: list of {"filename": ..., "page": <1-indexed>, "text": ...}
          for every page that had an extractable text layer.
        - scanned: list of (filename, pdf_bytes) for files that had NO
          extractable text at all (e.g. scanned/image-only PDFs). Callers
          should hand these to Gemini's native PDF reading instead, since
          pypdf cannot read text out of a page that's just an image.
    """
    pages: list[dict] = []
    scanned: list[tuple[str, bytes]] = []

    for filename, pdf_bytes in sources:
        file_pages: list[dict] = []
        try:
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            for page_index, page_obj in enumerate(reader.pages, start=1):
                text = (page_obj.extract_text() or "").strip()
                if text:
                    file_pages.append({"filename": filename, "page": page_index, "text": text})
        except Exception:
            file_pages = []

        if file_pages:
            pages.extend(file_pages)
        else:
            scanned.append((filename, pdf_bytes))

    return pages, scanned


def render_pdf_pages_as_images(pdf_bytes: bytes, dpi: int = 150, jpeg_quality: int = 82) -> list[bytes]:
    """
    Render every page of a PDF to a JPEG image.

    Used for scanned/image-only PDFs: sending the raw PDF file straight to
    Gemini can fail outright for large or complex scanned documents (a
    single-digit-hundred-MB engineering drawing set can trip Gemini's own
    PDF-processing limits with an opaque "invalid argument" error). Sending
    plain page images instead avoids that entirely, and lets us control the
    resolution/size ourselves.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        return [page.get_pixmap(matrix=matrix).tobytes("jpeg", jpg_quality=jpeg_quality) for page in doc]
    finally:
        doc.close()


def ocr_scanned_pdf(
    filename: str,
    pdf_bytes: bytes,
    dpi: int = 200,
    min_chars: int = 20,
    on_page: Callable[[int, int], None] | None = None,
) -> tuple[list[dict], list[tuple[int, bytes]]]:
    """
    Render every page of a scanned/image-only PDF and run local, free OCR
    (Tesseract, Japanese+English) on it, one time only — the result is
    meant to be cached by the caller.

    Pages where OCR extracts enough text come back as ordinary text page
    entries (same shape as split_text_and_scanned's), so they flow through
    the same cheap, quota-free text-citation pipeline as regular PDFs from
    then on. Pages where OCR yields little or no text (mostly diagrams or
    drawings with sparse/no printed text) come back separately as
    (page_number, jpeg_bytes) so the caller can still hand just those to
    Gemini's vision reading as a fallback. In practice this means only a
    small minority of pages of a typical scanned document need to be sent
    to Gemini at all, which is what keeps per-question payload size and
    free-tier quota usage down even for large scanned documents.

    on_page(page_index, total_pages), if given, is called after each page
    finishes OCR so callers can show progress for slow, many-page files.
    """
    import pytesseract
    from PIL import Image

    page_images = render_pdf_pages_as_images(pdf_bytes, dpi=dpi)
    total = len(page_images)

    ocr_pages: list[dict] = []
    fallback_images: list[tuple[int, bytes]] = []
    for page_index, image_bytes in enumerate(page_images, start=1):
        try:
            image = Image.open(io.BytesIO(image_bytes))
            text = pytesseract.image_to_string(image, lang="jpn+eng").strip()
        except Exception:
            text = ""
        if len(text) >= min_chars:
            ocr_pages.append({"filename": filename, "page": page_index, "text": text})
        else:
            fallback_images.append((page_index, image_bytes))
        if on_page:
            on_page(page_index, total)

    return ocr_pages, fallback_images


def build_context_block(pages: list[dict]) -> str:
    """
    Format extracted pages into a clearly labelled context string
    that Gemini can reason over and cite from.
    """
    if not pages:
        return "（テキストを抽出できた文書はありません）"
    parts: list[str] = []
    for entry in pages:
        header = f"[SOURCE: {entry['filename']}, Page {entry['page']}]"
        parts.append(f"{header}\n{entry['text']}")
    return "\n\n---\n\n".join(parts)
