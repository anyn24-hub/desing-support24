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
