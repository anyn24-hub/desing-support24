from __future__ import annotations

import os

import streamlit as st

from utils.gemini_client import ask_gemini
from utils.pdf_processor import extract_pages_from_pdfs

st.set_page_config(
    page_title="TechDoc スマートアシスタント",
    page_icon="📄",
    layout="wide",
)

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
ENV_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")


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


def render_sidebar() -> tuple[list, str]:
    with st.sidebar:
        if not ENV_GEMINI_API_KEY:
            st.session_state["gemini_api_key"] = st.text_input(
                "Gemini APIキー",
                type="password",
                value=st.session_state["gemini_api_key"],
                help="環境変数 GEMINI_API_KEY が未設定のため、ここに直接入力してください。",
            )
            st.divider()

        st.header("📁 ドキュメントライブラリ")
        uploaded_files = st.file_uploader(
            "PDF文書をアップロード",
            type=["pdf"],
            accept_multiple_files=True,
            help="1つ以上のPDF文書をアップロードしてください（仕様書、報告書、マニュアルなど）。",
        )

        if uploaded_files:
            st.success(f"{len(uploaded_files)} 件の文書を読み込みました")
            for f in uploaded_files:
                st.markdown(f"- `{f.name}`")

            cache_key = tuple(sorted(f.name + str(f.size) for f in uploaded_files))
            if st.session_state["pages_cache_key"] != cache_key:
                with st.spinner("文書を解析・インデックス作成しています…"):
                    st.session_state["pages"] = extract_pages_from_pdfs(uploaded_files)
                st.session_state["pages_cache_key"] = cache_key
        else:
            st.info("まだ文書がアップロードされていません。")
            st.session_state["pages"] = []
            st.session_state["pages_cache_key"] = None

        st.divider()

        st.header("💬 AIへの質問")
        with st.form("ai_question_form", clear_on_submit=True):
            question_input = st.text_input(
                "AIへの質問",
                placeholder="文書について質問を入力してください…",
                label_visibility="collapsed",
            )
            submitted = st.form_submit_button("送信", use_container_width=True)

        if submitted and question_input:
            st.session_state["messages"].append({"role": "user", "content": question_input})
            with st.spinner("AIが文書を解析しています…"):
                answer = ask_gemini(
                    question=question_input,
                    pages=st.session_state["pages"],
                    api_key=st.session_state["gemini_api_key"],
                    history=st.session_state["messages"][:-1],
                )
            st.session_state["messages"].append({"role": "assistant", "content": answer})
            st.rerun()

        st.divider()

        st.header("🔍 過去の質疑応答を検索")
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

    return uploaded_files, search_term


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


def main() -> None:
    if not check_password():
        return

    init_session_state()

    st.title("TechDoc スマートアシスタント")
    st.caption(
        "技術文書をアップロードし、内容について質問してください。"
        "すべての回答には正確な出典（ファイル名・ページ番号）が付与されます。"
    )

    uploaded_files, search_term = render_sidebar()

    if search_term:
        render_search_results(search_term)
        return

    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if not uploaded_files:
        st.info(
            "👈 サイドバーから1つ以上のPDF文書をアップロードして開始してください。"
            "その後、サイドバーの「AIへの質問」欄から自由に質問をご入力いただけます。"
        )
    elif not st.session_state["messages"]:
        st.info("👈 サイドバーの「AIへの質問」欄に質問を入力し、「送信」を押してください。")


if __name__ == "__main__":
    main()
