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

1. サイドバーの「📁 ドキュメントライブラリ」からPDFを1つ以上アップロード
2. サイドバーの「💬 AIへの質問」欄に質問を入力して「送信」
3. 回答には `[ファイル名, p.番号]` の形式で出典ページが明記される
4. 「🔍 過去の質疑応答を検索」で過去のやり取りをキーワード検索可能

## 構成

```
main.py                    # Streamlit UI本体
utils/
├── pdf_processor.py       # PDFからページ単位でテキスト抽出
└── gemini_client.py       # Gemini APIへの問い合わせ・出典付き回答生成
requirements.txt
.streamlit/config.toml     # 別サーバー/モバイル向けサーバー設定
.env.example
```
