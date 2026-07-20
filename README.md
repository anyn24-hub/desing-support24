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

文字情報を持たないPDF（スキャンした図面など）は、ライブラリ上で「スキャンPDF」と表示されます。**OCR（文字認識）していなくても、そのまま質問できます**（該当ページをJPEG画像としてGeminiに直接読み取らせます）。ただし大きいスキャン文書は画像データの送信量が増え、無料枠のAPIリクエスト消費もその分増えます。

ライブラリの一覧に表示される「🔍 OCR開始」ボタンを押すと、**アプリ側で無料のOCR（Tesseract）を実行**し、文字として読み取れたページを以後は通常のテキストPDFと同じ軽量な方式（テキストをそのままGeminiに渡す）に切り替えます。OCRで十分な文字が読み取れなかったページ（図面・写真中心のページなど）だけ、引き続き画像としてGeminiに読み取らせます。**OCRは自動実行されません**（数百ページ規模の文書だと時間がかかるため）。数ページずつ区切って処理し、進捗バーが表示されます。押した後アプリを閉じてしまってもキャッシュされた分は残るので、「🔍 OCR再開」で途中から続けられます。

質問文に特定のファイル名を含めると、画像として送信が必要なページについてはそのファイルの分だけが送信されます（他の資料も含めたい場合はファイル名を指定しなければ全資料が対象になります）。

サイドバーには、普段使わない設定のみをまとめています。

- Gemini APIキー（環境変数 `GEMINI_API_KEY` が未設定の場合のみ表示）
- 「🔍 過去の質疑応答を検索」でキーワード検索
- 「🗑️ 会話をクリア」

### Googleドライブ連携（任意）

`.env.example` の `GOOGLE_SERVICE_ACCOUNT_JSON` と `DRIVE_FOLDER_ID` を両方設定すると、「📁 ドキュメントライブラリ」内に「☁️ Googleドライブと同期」が表示されます。フォルダの内容は自動では取り込まれず、「🔄 今すぐ同期」を押したときだけ確認・取得します（既に同期済みのファイルは再ダウンロードしません）。フォルダにPDFを追加したら、その都度このボタンを押してください。設定手順は `.env.example` 内のコメントを参照してください。

### ドキュメントライブラリの保持について

アップロード・Googleドライブ経由のいずれで読み込んだ文書も、**アプリ全体で共有される1つのライブラリ**に保持されます。ブラウザを閉じて開き直しても、別の端末からアクセスしても、読み込み直す必要はありません。「🗑️ ライブラリをすべてクリア」で明示的にリセットできます。

（技術的な注記: これは`st.session_state`ではなく`st.cache_resource`によるアプリプロセス全体で共有されるキャッシュです。そのため、Streamlit Cloud側でアプリが長時間使われず休止・再起動した場合はリセットされ、次にドライブと同期するまで文書は空の状態に戻ります。複数人が同じURLにアクセスする場合、ライブラリは全員で共有される点にもご注意ください。）

## 構成

```
main.py                    # Streamlit UI本体
utils/
├── pdf_processor.py       # PDFからページ単位でテキスト抽出／スキャンPDFのOCR・ページ画像レンダリング
├── gemini_client.py       # Gemini APIへの問い合わせ・出典付き回答生成
└── drive_client.py        # Googleドライブ連携（フォルダ内PDFの一覧・取得）
requirements.txt
packages.txt                # デプロイ先のOS側パッケージ（tesseract-ocr等）
.streamlit/config.toml     # 別サーバー/モバイル向けサーバー設定
.env.example
```

### OCR（文字認識）のシステム要件について

スキャンPDFのOCRには [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) 本体（日本語データ含む）が必要です。

- **Streamlit Community Cloud**: リポジトリ直下の `packages.txt`（`tesseract-ocr`, `tesseract-ocr-jpn`）を自動的にaptでインストールするため、追加作業は不要です。
- **ローカル / その他のホスティング**: OS側で別途インストールが必要です（例: Debian/Ubuntu系なら `sudo apt-get install tesseract-ocr tesseract-ocr-jpn`）。未インストールの場合、スキャンPDFはOCRに失敗し、ライブラリ上に「OCR失敗」と表示されます（通常のテキストPDFの利用には影響しません）。
