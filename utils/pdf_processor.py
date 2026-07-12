from __future__ import annotations

import io
from typing import Any

import pypdf


def extract_pages_from_sources(sources: list[tuple[str, bytes]]) -> list[dict]:
    """
    Extract text from each page of every (filename, pdf_bytes) pair.

    Returns a list of dicts:
        {
            "filename": "design_spec.pdf",
            "page":     3,           # 1-indexed
            "text":     "..."
        }
    """
    pages: list[dict] = []
    for filename, pdf_bytes in sources:
        try:
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            for page_index, page_obj in enumerate(reader.pages, start=1):
                text = page_obj.extract_text() or ""
                text = text.strip()
                if text:
                    pages.append(
                        {
                            "filename": filename,
                            "page": page_index,
                            "text": text,
                        }
                    )
        except Exception as exc:
            pages.append(
                {
                    "filename": filename,
                    "page": 0,
                    "text": f"[Error extracting text: {exc}]",
                }
            )

    return pages


def extract_pages_from_pdfs(uploaded_files: list[Any]) -> list[dict]:
    """Extract pages from Streamlit's UploadedFile objects (st.file_uploader)."""
    sources: list[tuple[str, bytes]] = []
    for uploaded_file in uploaded_files:
        data = uploaded_file.read()
        uploaded_file.seek(0)
        sources.append((uploaded_file.name, data))
    return extract_pages_from_sources(sources)


def build_context_block(pages: list[dict]) -> str:
    """
    Format extracted pages into a clearly labelled context string
    that Gemini can reason over and cite from.
    """
    parts: list[str] = []
    for entry in pages:
        header = f"[SOURCE: {entry['filename']}, Page {entry['page']}]"
        parts.append(f"{header}\n{entry['text']}")
    return "\n\n---\n\n".join(parts)
