from __future__ import annotations

import io

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


def count_pdf_pages(pdf_bytes: bytes) -> int:
    """Cheap page count (reads the xref table only, no rendering) — used to
    show progress/totals for scanned PDFs without paying the cost of
    rasterizing every page up front."""
    return len(pypdf.PdfReader(io.BytesIO(pdf_bytes)).pages)


def render_pdf_page_range(
    pdf_bytes: bytes, start_page: int, end_page: int, dpi: int = 150, jpeg_quality: int = 82
) -> list[tuple[int, bytes]]:
    """
    Render a 1-indexed, inclusive page range to JPEG images (no OCR).

    Used as a fast fallback so a question about a scanned PDF never has to
    wait for OCR to finish: pages that haven't been OCR'd yet are rendered
    on demand, just for the range needed, instead of the whole document.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        return [
            (page_number, doc[page_number - 1].get_pixmap(matrix=matrix).tobytes("jpeg", jpg_quality=jpeg_quality))
            for page_number in range(start_page, end_page + 1)
        ]
    finally:
        doc.close()


def render_and_ocr_range(
    pdf_bytes: bytes,
    start_page: int,
    end_page: int,
    dpi: int = 200,
    jpeg_quality: int = 82,
    lang: str = "jpn+eng",
) -> list[tuple[int, bytes, str]]:
    """
    Render and OCR a 1-indexed, inclusive page range of a scanned PDF.

    Returns [(page_number, jpeg_bytes, ocr_text), ...]. Deliberately scoped
    to a small page range (rather than a whole document) so a caller can
    process a large scanned PDF in small batches across many Streamlit
    reruns instead of blocking a single script run on hundreds of pages of
    OCR at once — OCR is by far the slowest step here (real recognition
    work, roughly 1-5+ seconds per page), unlike plain rendering.
    """
    import fitz  # PyMuPDF
    import pytesseract
    from PIL import Image

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        results: list[tuple[int, bytes, str]] = []
        for page_number in range(start_page, end_page + 1):
            image_bytes = doc[page_number - 1].get_pixmap(matrix=matrix).tobytes("jpeg", jpg_quality=jpeg_quality)
            try:
                text = pytesseract.image_to_string(Image.open(io.BytesIO(image_bytes)), lang=lang).strip()
            except Exception:
                text = ""
            results.append((page_number, image_bytes, text))
        return results
    finally:
        doc.close()


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
