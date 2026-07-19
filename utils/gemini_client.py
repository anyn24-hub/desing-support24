from __future__ import annotations

import os
import tempfile
import time
from typing import Any

from google import genai

from utils.pdf_processor import build_context_block

_SYSTEM_PROMPT = """\
You are a highly specialised technical documentation assistant. You answer \
questions strictly based on the document excerpts and/or attached PDF files \
provided below.

RESPONSE LANGUAGE:
- Always respond in professional, clear Japanese (日本語), regardless of the \
language the question was asked in.

RESPONSE FORMAT — follow this exactly:
- The very first line of your response MUST be in the exact form:
  TITLE: <a concise Japanese title, roughly 10-20 characters, summarising \
the user's question>
- Leave one blank line after the TITLE line, then write your full answer \
below it, following all the rules below.

CITATION RULES — follow these exactly:
- Every claim must be traceable to a citation in the format: \
[<filename>, p.<page_number>], but do NOT repeat the same citation after \
every single sentence or bullet point — that makes the answer hard to read.
- Group related sentences or bullet points that come from the same page \
into one paragraph or list, and place a SINGLE citation at the end of that \
paragraph or list (e.g. after the last bullet), not after each line.
- Only add a new citation when the source file or page actually changes \
partway through the answer.
- Example (good): "定格圧力は155 barで、耐熱温度は200度です [design_spec.pdf, p.12]。"
- Example (bad — do not do this): "定格圧力は155 barです [design_spec.pdf, \
p.12]。耐熱温度は200度です [design_spec.pdf, p.12]。"
- If the same fact is supported by multiple pages, list all of them together, \
e.g. [design_spec.pdf, p.3, p.7].
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

# Flash is fast and cheap but tends to lose fine detail (small title-block
# text, symbol legends, dense diagrams) when it downsamples a large scanned
# page internally. Pro is slower/costlier but preserves far more visual
# detail, so it's worth the trade-off specifically when a scanned PDF is
# being read natively.
_TEXT_MODEL = "gemini-flash-latest"
_VISION_MODEL = "gemini-pro-latest"


def _get_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def upload_pdf_for_gemini(api_key: str, filename: str, pdf_bytes: bytes) -> Any:
    """
    Upload a PDF to Gemini's Files API so it can be read natively (including
    scanned/image-only pages that have no extractable text layer).

    Gemini processes an uploaded file asynchronously — right after upload it
    sits in a PROCESSING state and is not yet readable. This waits for it to
    become ACTIVE before returning, since handing a still-PROCESSING file to
    generate_content/send_message silently behaves as if it weren't there.
    """
    client = _get_client(api_key)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        file_obj = client.files.upload(
            file=tmp_path,
            config={"mime_type": "application/pdf", "display_name": filename},
        )
    finally:
        os.remove(tmp_path)

    while file_obj.state.name == "PROCESSING":
        time.sleep(1)
        file_obj = client.files.get(name=file_obj.name)

    if file_obj.state.name == "FAILED":
        raise RuntimeError(f"Gemini側でのファイル処理に失敗しました（{filename}）")

    return file_obj


def _split_title(text: str, fallback_title: str) -> tuple[str, str]:
    """Pull the `TITLE: ...` line the model was asked to lead with off of its
    response. Falls back to a truncated version of the question if the model
    didn't follow the format, so the UI always has something to show."""
    first_line, _, rest = text.partition("\n")
    if first_line.strip().upper().startswith("TITLE:"):
        title = first_line.split(":", 1)[1].strip()
        return (title or fallback_title, rest.lstrip("\n"))
    return (fallback_title, text)


def ask_gemini(
    question: str,
    pages: list[dict],
    api_key: str,
    scanned_files: list[tuple[str, Any]] | None = None,
    history: list[dict] | None = None,
) -> tuple[str, str]:
    """
    Send a question to Gemini with full document context (extracted text
    plus any natively-attached scanned PDFs) and conversation history.

    Returns (title, answer_markdown) — a short auto-generated title for the
    Q&A (for use as e.g. an expander label) and the answer body. Never
    raises — all failure modes are converted into a friendly Japanese
    message so a single bad request can't crash the app.
    """
    fallback_title = question.strip()[:30] or "質問"

    if not api_key:
        return (
            "APIキー未設定",
            "Gemini APIキーが設定されていません。サイドバー上部の入力欄にAPIキーを"
            "入力するか、環境変数 `GEMINI_API_KEY` を設定してください。",
        )

    if not pages and not scanned_files:
        return (
            "文書未読み込み",
            "文書が読み込まれていません。質問する前に、上の「ドキュメントライブラリ」から"
            "少なくとも1つのPDFをアップロードするか、Googleドライブとの同期をお待ちください。",
        )

    context = build_context_block(pages)
    system_instruction = _SYSTEM_PROMPT.format(context=context)

    gemini_history: list[dict] = []
    if history:
        for msg in history:
            role = "user" if msg["role"] == "user" else "model"
            gemini_history.append({"role": role, "parts": [{"text": msg["content"]}]})

    message_parts: list[Any] = [f"USER QUESTION:\n{question}"]
    for filename, file_obj in scanned_files or []:
        message_parts.append(f"添付PDFファイル名: {filename}")
        message_parts.append(file_obj)

    model_name = _VISION_MODEL if scanned_files else _TEXT_MODEL
    config = dict(_GENERATION_CONFIG, system_instruction=system_instruction)

    try:
        client = _get_client(api_key)
        chat = client.chats.create(model=model_name, config=config, history=gemini_history)
        response = chat.send_message(message_parts)
        return _split_title(response.text, fallback_title)
    except Exception as exc:
        return ("エラー", f"**Gemini APIとの通信中にエラーが発生しました：** {exc}")
