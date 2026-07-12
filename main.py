from __future__ import annotations

import os

import streamlit as st

from utils.gemini_client import ask_gemini
from utils.pdf_processor import extract_pages_from_sources

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
    st.session_state.setdefault("gemini_api_key", ENV_GEMINI_API_KEY)
    st.session_state.setdefault("drive_documents", {})  # name -> pdf bytes
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
    """Optional: load PDFs directly from a shared Google Drive folder."""
    from utils.drive_client import download_pdf, list_pdfs_in_folder

    st.markdown("**☁️ Googleドライブから読み込む**")

    col1, col2 = st.columns([3, 1])
    with col1:
        st.caption("指定フォルダ内のPDFを一覧表示して読み込めます。")
    with col2:
        if st.button("一覧を更新", use_container_width=True):
            st.session_state["drive_files_cache"] = None

    if st.session_state["drive_files_cache"] is None:
        try:
            with st.spinner("Googleドライブのフォルダを確認しています…"):
                st.session_state["drive_files_cache"] = list_pdfs_in_folder(
                    GOOGLE_SERVICE_ACCOUNT_JSON, DRIVE_FOLDER_ID
                )
        except Exception as exc:
            st.error(f"Googleドライブへの接続に失敗しました： {exc}")
            st.session_state["drive_files_cache"] = []

    drive_files = st.session_state["drive_files_cache"]
    if not drive_files:
        st.caption("フォルダ内にPDFが見つかりませんでした。")
        return

    selected_names = st.multiselect(
        "読み込むPDFを選択",
        options=[f["name"] for f in drive_files],
        default=list(st.session_state["drive_documents"].keys()),
    )

    if st.button("選択したPDFを読み込む", use_container_width=True):
        new_docs: dict[str, bytes] = {}
        try:
            with st.spinner("Googleドライブから取得しています…"):
                for f in drive_files:
                    if f["name"] not in selected_names:
                        continue
                    if f["name"] in st.session_state["drive_documents"]:
                        new_docs[f["name"]] = st.session_state["drive_documents"][f["name"]]
                    else:
                        new_docs[f["name"]] = download_pdf(GOOGLE_SERVICE_ACCOUNT_JSON, f["id"])
            st.session_state["drive_documents"] = new_docs
            st.rerun()
        except Exception as exc:
            st.error(f"PDFの取得に失敗しました： {exc}")


def render_document_library() -> list[tuple[str, bytes]]:
    """PDF upload lives directly on the main screen — no extra tap needed."""
    has_docs = bool(st.session_state["pages"])
    with st.expander("📁 ドキュメントライブラリ（PDFをアップロード）", expanded=not has_docs):
        uploaded_files = st.file_uploader(
            "PDF文書をアップロード",
            type=["pdf"],
            accept_multiple_files=True,
            help="1つ以上のPDF文書をアップロードしてください（仕様書、報告書、マニュアルなど）。",
        )

        if DRIVE_ENABLED:
            st.divider()
            render_drive_section()

        sources: list[tuple[str, bytes]] = [(f.name, f.getvalue()) for f in (uploaded_files or [])]
        sources += list(st.session_state["drive_documents"].items())

        if sources:
            st.divider()
            st.success(f"{len(sources)} 件の文書を読み込みました")
            for name, _ in sources:
                st.markdown(f"- `{name}`")

            cache_key = tuple(sorted(f"{name}:{len(data)}" for name, data in sources))
            if st.session_state["pages_cache_key"] != cache_key:
                with st.spinner("文書を解析・インデックス作成しています…"):
                    st.session_state["pages"] = extract_pages_from_sources(sources)
                st.session_state["pages_cache_key"] = cache_key
        else:
            st.info("まだ文書がアップロードされていません。")
            st.session_state["pages"] = []
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


def handle_question(question: str) -> None:
    st.session_state["messages"].append({"role": "user", "content": question})
    with st.spinner("AIが文書を解析しています…"):
        answer = ask_gemini(
            question=question,
            pages=st.session_state["pages"],
            api_key=st.session_state["gemini_api_key"],
            history=st.session_state["messages"][:-1],
        )
    st.session_state["messages"].append({"role": "assistant", "content": answer})
    st.rerun()


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

    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if not document_sources:
        st.info("👆 上の「ドキュメントライブラリ」からPDF文書をアップロードして開始してください。")
    elif not st.session_state["messages"]:
        st.info("👇 下の入力欄に質問を入力してください。")

    question = st.chat_input("AIへの質問")
    if question:
        handle_question(question)


if __name__ == "__main__":
    main()
