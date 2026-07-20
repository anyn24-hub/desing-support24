from __future__ import annotations

import os
import unicodedata

import streamlit as st

from utils.gemini_client import ask_gemini
from utils.pdf_processor import (
    count_pdf_pages,
    render_and_ocr_range,
    render_pdf_page_range,
    split_text_and_scanned,
)

st.set_page_config(
    page_title="TechDoc スマートアシスタント",
    page_icon="📄",
    layout="wide",
)

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
ENV_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
DRIVE_ENABLED = bool(DRIVE_FOLDER_ID and GOOGLE_SERVICE_ACCOUNT_JSON)

# Bumped with each fix so it's obvious at a glance (sidebar footer) whether
# a deployment actually picked up the latest code.
_BUILD_TAG = "2026-07-20-ocr-manual-chunked"

# How many pages of OCR to run per Streamlit script execution. Kept small
# and deliberately NOT automatic: OCR is real recognition work (roughly
# 1-5+ seconds per page), so running it unconditionally on every page load
# for a many-hundred-page scanned document would block the whole app —
# that's exactly what happened before this was made manual/chunked.
_OCR_PAGES_PER_RUN = 5
_OCR_MIN_CHARS = 20


@st.cache_resource(show_spinner=False)
def _document_store() -> dict:
    """
    A single document library shared by every visit to this running app
    instance (unlike st.session_state, which is private per browser tab).
    This is what lets documents loaded once stay loaded the next time
    someone opens the app, instead of resetting per session. It only resets
    if the app process itself restarts (e.g. after a long period of
    inactivity on Streamlit Cloud).
    """
    return {
        "uploaded_documents": {},  # name -> pdf bytes
        "drive_documents": {},  # file_id -> (name, pdf bytes)
        "drive_files_cache": None,  # [{"id", "name"}, ...], set by a manual sync
        "pages": [],
        "scanned_sources": [],  # [(name, pdf_bytes), ...] with no extractable text
        "pages_cache_key": None,
        # Scanned/image-only PDFs can be OCR'd locally (free, no Gemini
        # quota used), but only when the user explicitly starts it via a
        # button — see _process_ocr_chunk. Until/unless a page is OCR'd,
        # questions fall back to sending Gemini that page's image directly.
        "scanned_page_counts": {},  # name -> total page count (cheap, no rendering)
        "scanned_ocr_progress": {},  # name -> next 1-indexed page to OCR (> total == done)
        "scanned_ocr_pages": {},  # name -> [{"filename","page","text"}, ...] OCR'd so far
        "scanned_fallback_images": {},  # name -> [(page_number, jpeg_bytes), ...] OCR-poor pages
        "ocr_errors": {},  # name -> error string, for files OCR failed on entirely
    }


def check_password() -> bool:
    if not APP_PASSWORD:
        return True
    if st.session_state.get("authenticated"):
        return True

    st.title("TechDoc スマートアシスタント")
    st.markdown("このワークスペースは非公開です。アクセスパスワードを入力してください。")
    entered = st.text_input("パスワード", type="password", key="pw_input")
    if st.button("サインイン"):
        if entered == APP_PASSWORD:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("パスワードが正しくありません。もう一度お試しください。")
    return False


def init_session_state() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("gemini_api_key", ENV_GEMINI_API_KEY)


def _highlight(text: str, term: str) -> str:
    if not term:
        return text
    lower_text = text.lower()
    lower_term = term.lower()
    result = []
    start = 0
    idx = lower_text.find(lower_term)
    while idx != -1:
        result.append(text[start:idx])
        result.append(f"**:orange[{text[idx:idx + len(term)]}]**")
        start = idx + len(term)
        idx = lower_text.find(lower_term, start)
    result.append(text[start:])
    return "".join(result)


def render_settings_sidebar() -> str:
    """Secondary, rarely-used settings live in the sidebar (opt-in)."""
    with st.sidebar:
        st.header("⚙️ 設定")

        if not ENV_GEMINI_API_KEY:
            st.session_state["gemini_api_key"] = st.text_input(
                "Gemini APIキー",
                type="password",
                value=st.session_state["gemini_api_key"],
                help="環境変数 GEMINI_API_KEY が未設定のため、ここに直接入力してください。",
            )
            st.divider()

        st.subheader("🔍 過去の質疑応答を検索")
        search_term = st.text_input(
            "質問・回答を検索",
            key="search_term",
            placeholder="例：仕様、手順、基準 など…",
            label_visibility="collapsed",
        )

        st.divider()
        if st.button("🗑️ 会話をクリア", use_container_width=True):
            st.session_state["messages"] = []
            st.rerun()

        st.caption(f"build: {_BUILD_TAG}")

    return search_term


def render_drive_section() -> None:
    """Sync PDFs from a shared Google Drive folder — manual, on demand.

    Already-synced files (tracked by Drive file ID) are never re-downloaded;
    only files newly added to the folder since the last sync are fetched.
    """
    from utils.drive_client import download_pdf, list_pdfs_in_folder

    store = _document_store()

    st.markdown("**☁️ Googleドライブと同期**")
    st.caption(f"{len(store['drive_documents'])} 件を同期済み。フォルダにPDFを追加したら「今すぐ同期」を押してください。")

    if not st.button("🔄 今すぐ同期", use_container_width=True):
        return

    try:
        with st.spinner("Googleドライブのフォルダを確認しています…"):
            store["drive_files_cache"] = list_pdfs_in_folder(GOOGLE_SERVICE_ACCOUNT_JSON, DRIVE_FOLDER_ID)
    except Exception as exc:
        st.error(f"Googleドライブへの接続に失敗しました： {exc}")
        return

    drive_files = store["drive_files_cache"] or []
    new_files = [f for f in drive_files if f["id"] not in store["drive_documents"]]

    if not drive_files:
        st.caption("フォルダ内にPDFが見つかりませんでした。")
    elif not new_files:
        st.success("新しいPDFはありませんでした（既に最新の状態です）。")
    else:
        try:
            with st.spinner(f"新しいPDFを{len(new_files)}件取得しています…"):
                for f in new_files:
                    data = download_pdf(GOOGLE_SERVICE_ACCOUNT_JSON, f["id"])
                    store["drive_documents"][f["id"]] = (f["name"], data)
            st.success(f"{len(new_files)} 件の新しいPDFを取り込みました。")
        except Exception as exc:
            st.error(f"PDFの取得に失敗しました： {exc}")


def _forget_scanned_file(store: dict, name: str) -> None:
    store["scanned_page_counts"].pop(name, None)
    store["scanned_ocr_progress"].pop(name, None)
    store["scanned_ocr_pages"].pop(name, None)
    store["scanned_fallback_images"].pop(name, None)
    store["ocr_errors"].pop(name, None)


def _process_ocr_chunk(name: str, pdf_bytes: bytes) -> None:
    """Run OCR on the next small batch of pages of one scanned PDF, then
    rerun to pick up where it left off — this is what keeps a single
    Streamlit script execution short (bounded to _OCR_PAGES_PER_RUN pages)
    even for a document with hundreds of pages, instead of blocking the
    whole app for as long as full-document OCR takes."""
    store = _document_store()
    total = store["scanned_page_counts"].get(name)
    if total is None:
        try:
            total = count_pdf_pages(pdf_bytes)
            store["scanned_page_counts"][name] = total
        except Exception as exc:
            store["ocr_errors"][name] = str(exc)
            st.session_state.pop("ocr_active_file", None)
            return

    start_page = store["scanned_ocr_progress"].get(name, 1)
    if start_page > total:
        st.session_state.pop("ocr_active_file", None)
        return
    end_page = min(start_page + _OCR_PAGES_PER_RUN - 1, total)

    with st.spinner(f"📝 OCR処理中… {name}（{start_page}〜{end_page} / {total}ページ）"):
        try:
            results = render_and_ocr_range(pdf_bytes, start_page, end_page)
        except Exception as exc:
            store["ocr_errors"][name] = str(exc)
            st.session_state.pop("ocr_active_file", None)
            st.rerun()
            return

        for page_number, image_bytes, text in results:
            if len(text) >= _OCR_MIN_CHARS:
                store["scanned_ocr_pages"].setdefault(name, []).append(
                    {"filename": name, "page": page_number, "text": text}
                )
            else:
                store["scanned_fallback_images"].setdefault(name, []).append((page_number, image_bytes))
        store["scanned_ocr_progress"][name] = end_page + 1

    if end_page >= total:
        st.session_state.pop("ocr_active_file", None)
        st.success(f"✅ 「{name}」のOCRが完了しました。")
    st.rerun()


def render_document_library() -> list[tuple[str, bytes]]:
    """PDF upload lives directly on the main screen — no extra tap needed."""
    store = _document_store()
    has_docs = bool(store["pages"] or store["scanned_sources"])
    with st.expander("📁 ドキュメントライブラリ（PDFをアップロード）", expanded=not has_docs):
        uploaded_files = st.file_uploader(
            "PDF文書をアップロード",
            type=["pdf"],
            accept_multiple_files=True,
            help=(
                "1つ以上のPDF文書をアップロードしてください（仕様書、報告書、マニュアルなど）。"
                "大きいファイルはWi-Fi環境でのアップロードを推奨します。"
            ),
        )

        # Persist uploads into the shared library so they survive even if the
        # uploader widget's own selection later changes, or someone else
        # opens the app afterwards.
        for f in uploaded_files or []:
            store["uploaded_documents"][f.name] = f.getvalue()

        if DRIVE_ENABLED:
            st.divider()
            render_drive_section()

        sources: list[tuple[str, bytes]] = list(store["uploaded_documents"].items())
        sources += list(store["drive_documents"].values())

        if sources:
            st.divider()
            st.success(f"{len(sources)} 件の文書を読み込み済みです")

            cache_key = tuple(sorted(f"{name}:{len(data)}" for name, data in sources))
            if store["pages_cache_key"] != cache_key:
                with st.spinner("文書を解析・インデックス作成しています…"):
                    pages, scanned = split_text_and_scanned(sources)
                    store["pages"] = pages
                    store["scanned_sources"] = scanned
                store["pages_cache_key"] = cache_key
                # Documents changed — forget OCR progress for anything no
                # longer in the (possibly re-ordered/replaced) scanned set.
                current_names = {name for name, _ in scanned}
                for stale_name in list(store["scanned_ocr_progress"].keys()) + list(store["ocr_errors"].keys()):
                    if stale_name not in current_names:
                        _forget_scanned_file(store, stale_name)

            sources_by_name = dict(sources)
            scanned_names = {name for name, _ in store["scanned_sources"]}

            for name, _ in sources:
                if name not in scanned_names:
                    st.markdown(f"- `{name}`")
                    continue

                if name in store["ocr_errors"]:
                    st.markdown(f"- `{name}` _(スキャンPDF: OCR失敗 — 質問には画像として送信されます)_")
                    continue

                total = store["scanned_page_counts"].get(name)
                if total is None:
                    try:
                        total = count_pdf_pages(sources_by_name[name])
                        store["scanned_page_counts"][name] = total
                    except Exception as exc:
                        store["ocr_errors"][name] = str(exc)
                        st.markdown(f"- `{name}` _(スキャンPDF: 解析失敗)_")
                        continue

                progress = store["scanned_ocr_progress"].get(name, 1)
                done_pages = min(progress - 1, total)

                if done_pages >= total:
                    ocr_count = len(store["scanned_ocr_pages"].get(name, []))
                    fallback_count = len(store["scanned_fallback_images"].get(name, []))
                    st.markdown(
                        f"- `{name}` _(スキャンPDF: OCRで{ocr_count}ページを文字化、"
                        f"{fallback_count}ページは画像としてAIが直接解析)_"
                    )
                else:
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        if done_pages:
                            st.markdown(f"- `{name}` _(スキャンPDF: OCR未完了 {done_pages}/{total}ページ)_")
                            st.progress(done_pages / total)
                        else:
                            st.markdown(
                                f"- `{name}` _(スキャンPDF: 全{total}ページ・未OCR — "
                                "OCR無しでも質問は可能ですが、先にOCRしておくと以後の質問が速く・軽くなります)_"
                            )
                    with col2:
                        button_label = "🔍 OCR再開" if done_pages else "🔍 OCR開始"
                        if st.button(button_label, key=f"ocr_btn_{name}", use_container_width=True):
                            st.session_state["ocr_active_file"] = name
                            st.rerun()

            active_file = st.session_state.get("ocr_active_file")
            if active_file and active_file in scanned_names:
                _process_ocr_chunk(active_file, sources_by_name[active_file])

            if st.button("🗑️ ライブラリをすべてクリア"):
                store["uploaded_documents"] = {}
                store["drive_documents"] = {}
                store["drive_files_cache"] = None
                store["pages"] = []
                store["scanned_sources"] = []
                store["pages_cache_key"] = None
                store["scanned_page_counts"] = {}
                store["scanned_ocr_progress"] = {}
                store["scanned_ocr_pages"] = {}
                store["scanned_fallback_images"] = {}
                store["ocr_errors"] = {}
                st.session_state.pop("ocr_active_file", None)
                st.rerun()
        else:
            st.info("まだ文書がアップロードされていません。")
            store["pages"] = []
            store["scanned_sources"] = []
            store["pages_cache_key"] = None

    return sources


def render_search_results(search_term: str) -> None:
    all_messages = st.session_state["messages"]
    pairs = []
    for i in range(0, len(all_messages) - 1, 2):
        question = all_messages[i]
        answer = all_messages[i + 1] if i + 1 < len(all_messages) else None
        pairs.append((question, answer))

    matches = [
        (q, a)
        for q, a in pairs
        if search_term.lower() in q["content"].lower()
        or (a and search_term.lower() in a["content"].lower())
    ]

    st.subheader(f"🔍 「{search_term}」に一致する結果：{len(matches)} 件")
    if not matches:
        st.info("検索条件に一致する質問・回答は見つかりませんでした。")
    for q, a in matches:
        with st.chat_message("user"):
            st.markdown(_highlight(q["content"], search_term))
        if a:
            with st.chat_message("assistant"):
                st.markdown(_highlight(a["content"], search_term))
    st.divider()
    st.caption("サイドバーの検索欄をクリアすると、通常のチャット画面に戻ります。")


def _normalize_for_match(text: str) -> str:
    """NFKC-normalize (collapses full-width/half-width and other Unicode
    variants of the same visible character) and strip all whitespace, so
    filename matching isn't broken by mobile keyboard input quirks."""
    return "".join(unicodedata.normalize("NFKC", text).split())


def _filter_relevant_names(question: str, names: list[str]) -> list[str]:
    """If the question names one or more of the loaded scanned PDFs by
    filename, only use those — scanned engineering drawings can run to many
    pages, and rendering/sending every one of them on every question is
    wasteful. Falls back to all of them when the question doesn't reference
    a specific file (broad/general questions)."""
    normalized_question = _normalize_for_match(question)
    mentioned = [name for name in names if _normalize_for_match(name) in normalized_question]
    return mentioned if mentioned else names


def handle_question(question: str) -> None:
    store = _document_store()
    st.session_state["messages"].append({"role": "user", "content": question})
    with st.spinner("AIが文書を解析しています…"):
        all_pages = list(store["pages"])
        for pages in store["scanned_ocr_pages"].values():
            all_pages.extend(pages)

        scanned_by_name = dict(store["scanned_sources"])
        errors = [f"{name}: {err}" for name, err in store["ocr_errors"].items() if name in scanned_by_name]
        usable_names = [name for name in scanned_by_name if name not in store["ocr_errors"]]
        relevant_names = _filter_relevant_names(question, usable_names)

        fallback_images: list[tuple[str, list[tuple[int, bytes]]]] = []
        for name in relevant_names:
            pdf_bytes = scanned_by_name[name]
            already_processed = list(store["scanned_fallback_images"].get(name, []))
            total = store["scanned_page_counts"].get(name)
            progress = store["scanned_ocr_progress"].get(name, 1)

            not_yet_ocrd: list[tuple[int, bytes]] = []
            if total is None or progress <= total:
                try:
                    end = total if total is not None else count_pdf_pages(pdf_bytes)
                    store["scanned_page_counts"][name] = end
                    if progress <= end:
                        not_yet_ocrd = render_pdf_page_range(pdf_bytes, progress, end)
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
                    continue

            images = already_processed + not_yet_ocrd
            if images:
                fallback_images.append((name, images))

        if not all_pages and not fallback_images:
            title = "文書処理エラー" if errors else "文書未読み込み"
            answer = "以下の文書の処理に失敗しました：\n\n" + "\n".join(f"- {e}" for e in errors) if errors else (
                "文書が読み込まれていません。質問する前に、上の「ドキュメントライブラリ」から"
                "少なくとも1つのPDFをアップロードするか、Googleドライブとの同期をお待ちください。"
            )
        else:
            title, answer = ask_gemini(
                question=question,
                pages=all_pages,
                api_key=st.session_state["gemini_api_key"],
                scanned_images=fallback_images,
                history=st.session_state["messages"][:-1],
            )
            # Some scanned PDFs may have processed fine while others failed
            # entirely — always surface that instead of silently answering
            # as if every document were available, just because *something*
            # was.
            if errors:
                answer += "\n\n---\n⚠️ 以下の文書は処理に失敗したため、この回答には反映されていません：\n" + "\n".join(
                    f"- {e}" for e in errors
                )
    st.session_state["messages"].append({"role": "assistant", "content": answer, "title": title})
    st.rerun()


def render_chat_history() -> None:
    """Each Q&A pair collapses into its own section labelled with a short
    AI-generated title, so past questions can be scanned at a glance while
    still being free to ask the next one."""
    messages = st.session_state["messages"]
    pairs = [(messages[i], messages[i + 1] if i + 1 < len(messages) else None) for i in range(0, len(messages), 2)]

    for idx, (user_msg, assistant_msg) in enumerate(pairs):
        title = (assistant_msg or {}).get("title") or user_msg["content"][:30]
        is_latest = idx == len(pairs) - 1
        with st.expander(f"💬 {title}", expanded=is_latest):
            with st.chat_message("user"):
                st.markdown(user_msg["content"])
            if assistant_msg:
                with st.chat_message("assistant"):
                    st.markdown(assistant_msg["content"])


def main() -> None:
    if not check_password():
        return

    init_session_state()

    st.title("TechDoc スマートアシスタント")
    st.caption(
        "技術文書をアップロードし、内容について質問してください。"
        "すべての回答には正確な出典（ファイル名・ページ番号）が付与されます。"
    )

    search_term = render_settings_sidebar()
    document_sources = render_document_library()

    if search_term:
        render_search_results(search_term)
        return

    render_chat_history()

    if not document_sources:
        st.info("👆 上の「ドキュメントライブラリ」からPDF文書をアップロードして開始してください。")
    elif not st.session_state["messages"]:
        st.info("👇 下の入力欄に質問を入力してください。")

    question = st.chat_input("AIへの質問")
    if question:
        handle_question(question)


if __name__ == "__main__":
    main()
