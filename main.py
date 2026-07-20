from __future__ import annotations

import os
import unicodedata

import streamlit as st

from utils.gemini_client import ask_gemini
from utils.pdf_processor import render_pdf_pages_as_images, split_text_and_scanned

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
_BUILD_TAG = "2026-07-19-page-cap-citations"


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
        "scanned_page_images": {},  # name -> [page1_jpeg_bytes, page2_jpeg_bytes, ...]
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

            scanned_names = {name for name, _ in store["scanned_sources"]}
            for name, _ in sources:
                if name in scanned_names:
                    st.markdown(f"- `{name}` _(スキャンPDF: 画像として解析されます)_")
                else:
                    st.markdown(f"- `{name}`")

            if st.button("🗑️ ライブラリをすべてクリア"):
                store["uploaded_documents"] = {}
                store["drive_documents"] = {}
                store["drive_files_cache"] = None
                store["pages"] = []
                store["scanned_sources"] = []
                store["pages_cache_key"] = None
                store["scanned_page_images"] = {}
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


def _render_scanned_images() -> tuple[list[tuple[str, list[bytes]]], list[str]]:
    """Render each scanned/image-only PDF to page images once, caching the
    result in the shared document store so repeat questions (and other
    sessions) don't re-render.

    Returns (rendered (name, [page_images]) pairs, error messages) —
    rendering failures are surfaced rather than silently skipped."""
    store = _document_store()
    cache = store["scanned_page_images"]
    scanned_sources = store["scanned_sources"]

    current_names = {name for name, _ in scanned_sources}
    for stale_name in list(cache.keys()):
        if stale_name not in current_names:
            del cache[stale_name]

    errors: list[str] = []
    for name, data in scanned_sources:
        if name not in cache:
            try:
                cache[name] = render_pdf_pages_as_images(data)
            except Exception as exc:
                errors.append(f"{name}: {exc}")

    return [(name, cache[name]) for name in cache], errors


def _normalize_for_match(text: str) -> str:
    """NFKC-normalize (collapses full-width/half-width and other Unicode
    variants of the same visible character) and strip all whitespace, so
    filename matching isn't broken by mobile keyboard input quirks."""
    return "".join(unicodedata.normalize("NFKC", text).split())


def _filter_relevant_scanned_images(
    question: str, scanned_images: list[tuple[str, list[bytes]]]
) -> list[tuple[str, list[bytes]]]:
    """If the question names one or more of the loaded scanned PDFs by
    filename, only send those — scanned engineering drawings can run to
    many pages, and sending every one of them on every question is wasteful.
    Falls back to sending all of them when the question doesn't reference a
    specific file (broad/general questions)."""
    normalized_question = _normalize_for_match(question)
    mentioned = [
        (name, images) for name, images in scanned_images if _normalize_for_match(name) in normalized_question
    ]
    return mentioned if mentioned else scanned_images


_MAX_SCANNED_PAGES_PER_REQUEST = 60


def _cap_scanned_images(
    scanned_images: list[tuple[str, list[bytes]]]
) -> tuple[list[tuple[str, list[bytes]]], bool]:
    """Cap the total number of page images sent in a single request.
    Sending hundreds of pages at once (e.g. a broad question that pulls in
    every scanned document) is wasteful and can trip Gemini's rate/size
    limits. Returns (capped_images, was_truncated)."""
    total_pages = sum(len(images) for _, images in scanned_images)
    if total_pages <= _MAX_SCANNED_PAGES_PER_REQUEST:
        return scanned_images, False

    capped: list[tuple[str, list[bytes]]] = []
    remaining = _MAX_SCANNED_PAGES_PER_REQUEST
    for name, images in scanned_images:
        if remaining <= 0:
            break
        capped.append((name, images[:remaining]))
        remaining -= len(images[:remaining])
    return capped, True


def handle_question(question: str) -> None:
    store = _document_store()
    st.session_state["messages"].append({"role": "user", "content": question})
    with st.spinner("AIが文書を解析しています…"):
        scanned_images, render_errors = _render_scanned_images()
        scanned_images = _filter_relevant_scanned_images(question, scanned_images)
        scanned_images, truncated = _cap_scanned_images(scanned_images)

        if not store["pages"] and not scanned_images:
            title = "画像化エラー" if render_errors else "文書未読み込み"
            answer = "スキャンPDFの画像化に失敗しました：\n\n" + "\n".join(
                f"- {e}" for e in render_errors
            ) if render_errors else (
                "文書が読み込まれていません。質問する前に、上の「ドキュメントライブラリ」から"
                "少なくとも1つのPDFをアップロードするか、Googleドライブとの同期をお待ちください。"
            )
        else:
            title, answer = ask_gemini(
                question=question,
                pages=store["pages"],
                api_key=st.session_state["gemini_api_key"],
                scanned_images=scanned_images,
                history=st.session_state["messages"][:-1],
            )
            # Some scanned PDFs may have rendered fine while others failed —
            # always surface that instead of silently answering as if every
            # document were available, just because *something* was.
            if render_errors:
                answer += "\n\n---\n⚠️ 以下のスキャンPDFは画像化に失敗したため、この回答には反映されていません：\n" + "\n".join(
                    f"- {e}" for e in render_errors
                )
            if truncated:
                answer += (
                    "\n\n---\n⚠️ スキャン資料の総ページ数が多いため、一部のページのみを参照して回答しています。"
                    "特定の資料名を質問に含めると、その資料をより多く参照できます。"
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
