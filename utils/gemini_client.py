from __future__ import annotations

import google.generativeai as genai

from utils.pdf_processor import build_context_block

_SYSTEM_PROMPT = """\
You are a highly specialised technical documentation assistant. You answer \
questions strictly based on the document excerpts provided below.

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

DOCUMENT EXCERPTS:
{context}
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


def ask_gemini(
    question: str,
    pages: list[dict],
    api_key: str,
    history: list[dict] | None = None,
) -> str:
    """
    Send a question to Gemini with full document context and conversation
    history. Returns the answer as a markdown string. Never raises — all
    failure modes are converted into a friendly Japanese message so a single
    bad request can't crash the app.
    """
    if not api_key:
        return (
            "Gemini APIキーが設定されていません。サイドバー上部の入力欄にAPIキーを"
            "入力するか、環境変数 `GEMINI_API_KEY` を設定してください。"
        )

    if not pages:
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

    full_message = f"{system_with_context}\n\nUSER QUESTION:\n{question}"

    try:
        model = _get_model(api_key)
        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(full_message)
        return response.text
    except Exception as exc:
        return f"**Gemini APIとの通信中にエラーが発生しました：** {exc}"
