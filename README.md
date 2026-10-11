# 日本酒 糖度測定＆総合スペック管理ツール

## ファイル

- `app.py`: Streamlit画面、ラベル画像のAI解析、一覧・編集・比較・JSON復元
- `sake_brix_core.py`: SQLiteスキーマ、旧DB移行、保存・復元の共通処理
- `test_sake_brix_core.py`: DB・移行・復元処理の自動テスト

## 起動

Python 3.10以降を推奨します。

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS/Linux
# source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

初回起動時に、同じフォルダーへ `sake_brix.db` が作成されます。**既存アプリから切り替える場合は、`app.py` と `sake_brix_core.py` を現在の `sake_brix.db` と同じフォルダーに置いてください。** ZIPを別フォルダーに展開したまま起動すると、新しいDBが作成され、元データが表示されません。

既存DBにスキーマ移行が必要な場合は、移行前のバックアップを作成し、バックアップが検証できてから移行します。`measurements_old` がすでに存在する場合は、自動削除せずに起動を停止します。実データで起動する前に、DBファイルのコピーで移行を試すことを推奨します。

## Gemini API

サイドバーにAPIキーを入力するか、`.streamlit/secrets.toml` に次のように設定してください。

```toml
GEMINI_API_KEY = "あなたのAPIキー"
GEMINI_MODEL = "利用可能なモデル名"
```

使用できるモデル名は、Google AI Studio / Gemini API の利用権限・提供状況に応じて指定してください。AIを設定しなくても、手入力、DB保存、検索、比較、JSONバックアップ・復元は利用できます。

## データ保護

- DBファイルと自動移行バックアップは `app.py` と同じフォルダーに作成されます。
- 実データで移行を試す前に、`sake_brix.db` とバックアップを別の場所にコピーしてください。
- JSONバックアップは定期的にPC外にも保存してください。
- クラウドホスティングでは、ローカルファイルが再起動・再デプロイで失われる場合があります。この構成だけでクラウド上の永続保存は保証されません。継続運用する場合は永続ボリュームまたはマネージドDBが必要です。

## 自動テスト

```bash
python -m unittest -v test_sake_brix_core.py
```

テストはDBスキーマ、統計計算、旧DB移行、移行失敗時のロールバック、JSON復元の重複防止を検証します。Streamlit画面と実際のGemini API通信は別途、動作環境で確認してください。
