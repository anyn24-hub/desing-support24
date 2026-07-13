# TechDoc スマートアシスタント

アップロードしたPDF技術文書についてGemini APIに質問すると、根拠となるファイル名・ページ番号付きで日本語の回答を返すStreamlitアプリです。

## セットアップ

```bash
pip install -r requirements.txt
cp .env.example .env
# .env を編集して GEMINI_API_KEY を設定（未設定でもサイドバーから入力可能）
```

## 起動方法

### スマホのローカル環境（同一Wi-Fi内）で使う場合

```bash
export $(cat .env | xargs)  # .envを使う場合
streamlit run main.py
```

起動後、PCと同じWi-FiにつながったスマホのブラウザからPCのローカルIPにアクセスします（例: `http://192.168.1.10:8501`）。PCのIPアドレスは `ipconfig`（Windows）や `ifconfig` / `ip a`（Mac/Linux）で確認できます。`.streamlit/config.toml` で `server.address = "0.0.0.0"` を設定済みのため、追加の起動オプションは不要です。

### Termux等スマホ単体のローカル環境で使う場合

```bash
pip install -r requirements.txt
streamlit run main.py --server.port 8501
```

起動後、スマホ内のブラウザで `http://localhost:8501` を開きます。

### 別サーバー（Streamlit Community Cloud / Render / Railway 等）にデプロイする場合

1. このリポジトリを接続し、起動コマンド（Main file path）に `main.py` を指定
2. 環境変数 `GEMINI_API_KEY`（必須）と `APP_PASSWORD`（任意）をホスティング先のSecrets/環境変数に設定
3. `requirements.txt` を依存パッケージとして指定

## 使い方

PDFアップロード・質問入力・過去の会話は、すべてメイン画面から直接操作できます（サイドバーを開く必要はありません）。

1. メイン画面上部の「📁 ドキュメントライブラリ」からPDFを1つ以上アップロード（または下記のGoogleドライブ連携を設定していれば、フォルダから選んで読み込み）
2. 画面下部の「AIへの質問」欄に質問を入力してEnter
3. 回答には `[ファイル名, p.番号]` の形式で出典ページが明記される

質問ごとに、AIが自動生成した短いタイトルの付いた折りたたみセクションとして表示されます。最新の回答だけが開いた状態になり、過去の質問はタイトルだけを見て探せます。続けて次の質問を入力すればそのまま新しいセクションが追加されます。

文字情報を持たないPDF（スキャンした図面など）は、ライブラリ上で「スキャンPDF」と表示されます。この場合はテキスト抽出ではなく、Gemini自身がPDFを画像として直接読み取って回答します。

サイドバーには、普段使わない設定のみをまとめています。

- Gemini APIキー（環境変数 `GEMINI_API_KEY` が未設定の場合のみ表示）
- 「🔍 過去の質疑応答を検索」でキーワード検索
- 「🗑️ 会話をクリア」

### Googleドライブ連携（任意）

`.env.example` の `GOOGLE_SERVICE_ACCOUNT_JSON` と `DRIVE_FOLDER_ID` を両方設定すると、「📁 ドキュメントライブラリ」内に「☁️ Googleドライブと同期」が表示されます。指定フォルダ内のPDFは**選択操作なしで自動的に読み込まれ**、フォルダに新しいPDFが追加されたときはその分だけ自動で取り込まれます（既に読み込み済みのファイルは再ダウンロードされません）。設定手順は `.env.example` 内のコメントを参照してください。

アップロード・Googleドライブ経由のいずれで読み込んだ文書も、ブラウザセッション中はライブラリに保持され続けます（アップロード欄の見た目が変わっても消えません）。「🗑️ ライブラリをすべてクリア」で明示的にリセットできます。なお、これはブラウザセッション単位の保持であり、アプリの完全な再起動（別端末からのアクセスや長時間の休止後など）をまたいだ永続化はされません。Googleドライブに置いたファイルは常にドライブ側から自動的に再取得されます。

## 構成

```
main.py                    # Streamlit UI本体
utils/
├── pdf_processor.py       # PDFからページ単位でテキスト抽出
├── gemini_client.py       # Gemini APIへの問い合わせ・出典付き回答生成
└── drive_client.py        # Googleドライブ連携（フォルダ内PDFの一覧・取得）
requirements.txt
.streamlit/config.toml     # 別サーバー/モバイル向けサーバー設定
.env.example
```
