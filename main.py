from __future__ import annotations

import os

import streamlit as st

from utils.gemini_client import ask_gemini, upload_pdf_for_gemini
from utils.pdf_processor import split_text_and_scanned

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
    st.session_state.setdefault("pages_cache_key", None)
    st.session_state.setdefault("pages", [])
    st.session_state.setdefault("scanned_sources", [])  # [(name, pdf_bytes), ...] with no extractable text
    st.session_state.setdefault("gemini_uploaded_files", {})  # name -> Gemini File object (upload cache)
    st.session_state.setdefault("gemini_api_key", ENV_GEMINI_API_KEY)
    st.session_state.setdefault("uploaded_documents", {})  # name -> pdf bytes (persists across widget resets)
    st.session_state.setdefault("drive_documents", {})  # file_id -> (name, pdf bytes)
    st.session_state.setdefault("drive_files_cache", None)  # [{"id", "name"}, ...]


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

    return search_term


def render_drive_section() -> None:
    """Auto-sync PDFs from a shared Google Drive folder — no manual selection needed.

    Already-synced files (tracked by Drive file ID) are never re-downloaded;
    only files newly added to the folder since the last check are fetched.
    """
    from utils.drive_client import download_pdf, list_pdfs_in_folder

    st.markdown("**☁️ Googleドライブと同期**")

    _, refresh_col = st.columns([3, 1])
    with refresh_col:
        if st.button("再同期", use_container_width=True):
            st.session_state["drive_files_cache"] = None

    if st.session_state["drive_files_cache"] is None:
        try:
            with st.spinner("Googleドライブのフォルダを確認しています…"):
                st.session_state["drive_files_cache"] = list_pdfs_in_folder(
                    GOOGLE_SERVICE_ACCOUNT_JSON, DRIVE_FOLDER_ID
                )
        except Exception as exc:
            st.error(f"Googleドライブへの接続に失敗しました： {exc}")
            return

    drive_files = st.session_state["drive_files_cache"] or []
    new_files = [f for f in drive_files if f["id"] not in st.session_state["drive_documents"]]

    if new_files:
        try:
            with st.spinner(f"新しいPDFを{len(new_files)}件取得しています…"):
                for f in new_files:
                    data = download_pdf(GOOGLE_SERVICE_ACCOUNT_JSON, f["id"])
                    st.session_state["drive_documents"][f["id"]] = (f["name"], data)
        except Exception as exc:
            st.error(f"PDFの取得に失敗しました： {exc}")

    st.caption(f"{len(st.session_state['drive_documents'])} 件を同期済み。新しいPDFは自動で取り込まれます。")

    if not drive_files:
        st.caption("フォルダ内にPDFが見つかりませんでした。")


def render_document_library() -> list[tuple[str, bytes]]:
    """PDF upload lives directly on the main screen — no extra tap needed."""
    has_docs = bool(st.session_state["pages"])
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

        # Persist uploads into the library so they survive even if the
        # uploader widget's own selection later changes.
        for f in uploaded_files or []:
            st.session_state["uploaded_documents"][f.name] = f.getvalue()

        if DRIVE_ENABLED:
            st.divider()
            render_drive_section()

        sources: list[tuple[str, bytes]] = list(st.session_state["uploaded_documents"].items())
        sources += list(st.session_state["drive_documents"].values())

        if sources:
            st.divider()
            st.success(f"{len(sources)} 件の文書を読み込み済みです")

            cache_key = tuple(sorted(f"{name}:{len(data)}" for name, data in sources))
            if st.session_state["pages_cache_key"] != cache_key:
                with st.spinner("文書を解析・インデックス作成しています…"):
                    pages, scanned = split_text_and_scanned(sources)
                    st.session_state["pages"] = pages
                    st.session_state["scanned_sources"] = scanned
                st.session_state["pages_cache_key"] = cache_key

            scanned_names = {name for name, _ in st.session_state["scanned_sources"]}
            for name, _ in sources:
                if name in scanned_names:
                    st.markdown(f"- `{name}` _(スキャンPDF: 画像として解析されます)_")
                else:
                    st.markdown(f"- `{name}`")

            if st.button("🗑️ ライブラリをすべてクリア"):
                st.session_state["uploaded_documents"] = {}
                st.session_state["drive_documents"] = {}
                st.session_state["drive_files_cache"] = None
                st.session_state["scanned_sources"] = []
                st.session_state["gemini_uploaded_files"] = {}
                st.rerun()
        else:
            st.info("まだ文書がアップロードされていません。")
            st.session_state["pages"] = []
            st.session_state["scanned_sources"] = []
            st.session_state["pages_cache_key"] = None

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


def _sync_scanned_files_with_gemini(api_key: str) -> list[tuple[str, object]]:
    """Upload each scanned/image-only PDF to Gemini once, caching the result
    for the rest of the session so repeat questions don't re-upload."""
    cache = st.session_state["gemini_uploaded_files"]
    scanned_sources = st.session_state["scanned_sources"]

    current_names = {name for name, _ in scanned_sources}
    for stale_name in list(cache.keys()):
        if stale_name not in current_names:
            del cache[stale_name]

    if not api_key:
        return []

    for name, data in scanned_sources:
        if name not in cache:
            try:
                cache[name] = upload_pdf_for_gemini(api_key, name, data)
            except Exception:
                continue  # skip this file; the rest of the question can still proceed

    return [(name, cache[name]) for name in cache]


def handle_question(question: str) -> None:
    st.session_state["messages"].append({"role": "user", "content": question})
    with st.spinner("AIが文書を解析しています…"):
        scanned_files = _sync_scanned_files_with_gemini(st.session_state["gemini_api_key"])
        title, answer = ask_gemini(
            question=question,
            pages=st.session_state["pages"],
            api_key=st.session_state["gemini_api_key"],
            scanned_files=scanned_files,
            history=st.session_state["messages"][:-1],
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
