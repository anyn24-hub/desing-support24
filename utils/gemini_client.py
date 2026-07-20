from __future__ import annotations

from typing import Any

from google import genai
from google.genai import types

from utils.pdf_processor import build_context_block

_SYSTEM_PROMPT = """\
You are a highly specialised technical documentation assistant. You answer \
questions strictly based on the document excerpts and/or attached page \
images provided below.

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
- If your ENTIRE answer is based on a single source document, citations can \
be light — it's enough to name the source once (e.g. in a closing note like \
"（出典: design_spec.pdf, p.3-5）"), since there's no ambiguity about where \
the information came from. Do not repeat a citation after every sentence or \
bullet in this case.
- If your answer draws from MULTIPLE different source documents, cite \
clearly enough that the reader can tell which document each part came from \
— group related sentences/bullets from the same page into one paragraph or \
list with a single citation [<filename>, p.<page_number>] at the end of \
that paragraph/list, adding a new citation only when the source file or \
page actually changes partway through the answer.
- Example (single source, light citation): "定格圧力は155 barで、耐熱温度は \
200度です。（出典: design_spec.pdf, p.12）"
- Example (multiple sources, per-section citation): "定格圧力は155 barです \
[design_spec.pdf, p.12]。関連する保守基準は年1回の点検を求めています \
[maintenance_manual.pdf, p.4]。"
- If the same fact is supported by multiple pages, list all of them together, \
e.g. [design_spec.pdf, p.3, p.7].
- If the answer cannot be found in the provided documents, say so clearly in \
Japanese and do NOT fabricate information.
- Never answer from general knowledge alone when the documents are relevant; \
always tie your answer to the source material.

DOCUMENT EXCERPTS (extracted text, tagged by source file and page):
{context}

Some source PDFs have no machine-readable text layer (e.g. scanned drawings \
or image-only pages). For those, each page is attached below as an image — \
read it directly, including any diagrams, tables, or text visible in it, \
and apply the same citation rules using the filename and page number shown \
immediately before that page's image.
"""

_GENERATION_CONFIG = {
    "temperature": 0.1,
    "top_p": 0.95,
    "max_output_tokens": 4096,
}

_TEXT_MODEL = "gemini-flash-latest"


def _get_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


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
    scanned_images: list[tuple[str, list[tuple[int, bytes]]]] | None = None,
    history: list[dict] | None = None,
) -> tuple[str, str]:
    """
    Send a question to Gemini with full document context (extracted/OCR'd
    text plus, for the minority of scanned pages local OCR couldn't read
    enough text from, rendered page images) and conversation history.

    scanned_images: [(filename, [(page_number, jpeg_bytes), ...]), ...]
    These are only the pages local OCR failed to extract enough text from
    (see utils.pdf_processor.ocr_scanned_pdf) — most pages of a scanned
    document are handled as free OCR'd text via `pages` instead. Pages are
    sent as plain inline JPEG images rather than as whole PDF files, since
    large/complex scanned PDFs can trip Gemini's own PDF-processing limits
    with an opaque "invalid argument" error; small per-page images sidestep
    that and let us control resolution ourselves.

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

    if not pages and not scanned_images:
        return (
            "文書未読み込み",
            "文書が読み込まれていません。質問する前に、上の「ドキュメントライブラリ」から"
            "少なくとも1つのPDFをアップロードするか、Googleドライブとの同期をお待ちください。",
        )

    context = build_context_block(pages)
    system_instruction = _SYSTEM_PROMPT.format(context=context)

    history_text = ""
    if history:
        lines = [f"{'ユーザー' if msg['role'] == 'user' else 'AI'}: {msg['content']}" for msg in history]
        history_text = "これまでの会話:\n" + "\n".join(lines) + "\n\n"

    contents: list[Any] = [f"{history_text}USER QUESTION:\n{question}"]
    page_count = 0
    for filename, page_entries in scanned_images or []:
        for page_number, image_bytes in page_entries:
            contents.append(f"添付ページ画像: {filename}, p.{page_number}")
            contents.append(
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type="image/jpeg",
                    media_resolution="MEDIA_RESOLUTION_HIGH",
                )
            )
            page_count += 1

    config = dict(_GENERATION_CONFIG, system_instruction=system_instruction)

    try:
        client = _get_client(api_key)
        response = client.models.generate_content(model=_TEXT_MODEL, contents=contents, config=config)
        return _split_title(response.text, fallback_title)
    except Exception as exc:
        detail = str(exc)
        if scanned_images:
            total_bytes = sum(len(img) for _, entries in scanned_images for _, img in entries)
            detail += f"\n\n[診断情報]\n添付ページ数={page_count}, 合計サイズ={total_bytes / 1_000_000:.1f}MB"
        return ("エラー", f"**Gemini APIとの通信中にエラーが発生しました：** {detail}")
