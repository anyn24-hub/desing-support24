from __future__ import annotations

import os
import tempfile
from typing import Any

import google.generativeai as genai

from utils.pdf_processor import build_context_block

_SYSTEM_PROMPT = """\
You are a highly specialised technical documentation assistant. You answer \
questions strictly based on the document excerpts and/or attached PDF files \
provided below.

RESPONSE LANGUAGE:
- Always respond in professional, clear Japanese (日本語), regardless of the \
language the question was asked in.

CITATION RULES — follow these exactly:
- Every factual claim, number, requirement, or technical statement MUST be \
followed by an inline citation in the format: [<filename>, p.<page_number>]
- Example: "定格圧力は155 barです [design_spec.pdf, p.12]。"
- If the same fact is supported by multiple pages, list all of them.
- If the answer cannot be found in the provided documents, say so clearly in \
Japanese and do NOT fabricate information.
- Never answer from general knowledge alone when the documents are relevant; \
always tie your answer to the source material.

DOCUMENT EXCERPTS (extracted text, tagged by source file and page):
{context}

Some source PDFs have no machine-readable text layer (e.g. scanned drawings \
or image-only pages). For those, the original PDF file is attached below — \
read it directly, including any diagrams, tables, or text visible in the \
images, and apply the same citation rules using the filename shown \
immediately before each attached file and the page number you determine \
from the file itself.
"""

_GENERATION_CONFIG = {
    "temperature": 0.1,
    "top_p": 0.95,
    "max_output_tokens": 4096,
}


def _get_model(api_key: str) -> genai.GenerativeModel:
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        generation_config=_GENERATION_CONFIG,
    )


def upload_pdf_for_gemini(api_key: str, filename: str, pdf_bytes: bytes) -> Any:
    """
    Upload a PDF to Gemini's Files API so it can be read natively (including
    scanned/image-only pages that have no extractable text layer).
    """
    genai.configure(api_key=api_key)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        return genai.upload_file(path=tmp_path, display_name=filename, mime_type="application/pdf")
    finally:
        os.remove(tmp_path)


def ask_gemini(
    question: str,
    pages: list[dict],
    api_key: str,
    scanned_files: list[tuple[str, Any]] | None = None,
    history: list[dict] | None = None,
) -> str:
    """
    Send a question to Gemini with full document context (extracted text
    plus any natively-attached scanned PDFs) and conversation history.
    Returns the answer as a markdown string. Never raises — all failure
    modes are converted into a friendly Japanese message so a single bad
    request can't crash the app.
    """
    if not api_key:
        return (
            "Gemini APIキーが設定されていません。サイドバー上部の入力欄にAPIキーを"
            "入力するか、環境変数 `GEMINI_API_KEY` を設定してください。"
        )

    if not pages and not scanned_files:
        return (
            "文書が読み込まれていません。質問する前に、上の「ドキュメントライブラリ」から"
            "少なくとも1つのPDFをアップロードするか、Googleドライブとの同期をお待ちください。"
        )

    context = build_context_block(pages)
    system_with_context = _SYSTEM_PROMPT.format(context=context)

    gemini_history: list[dict] = []
    if history:
        for msg in history:
            role = "user" if msg["role"] == "user" else "model"
            gemini_history.append({"role": role, "parts": [msg["content"]]})

    message_parts: list[Any] = [f"{system_with_context}\n\nUSER QUESTION:\n{question}"]
    for filename, file_obj in scanned_files or []:
        message_parts.append(f"添付PDFファイル名: {filename}")
        message_parts.append(file_obj)

    try:
        model = _get_model(api_key)
        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(message_parts)
        return response.text
    except Exception as exc:
        return f"**Gemini APIとの通信中にエラーが発生しました：** {exc}"
