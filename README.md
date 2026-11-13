# Job-Hunting-Agent

就活メールを自動で読み取り、選考スケジュールを Google カレンダーとローカルのダッシュボードに登録するエージェントです。

Gmail から就活関連メールを取得し、ローカル LLM（Ollama）で企業名・イベント種別・日時を抽出して、カレンダー登録と企業ごとの選考状況管理までを自動で行います。

## 主な機能

- **Gmail 自動取込**: 件名または本文に就活キーワード（面接・選考・説明会・ES・内定など）を含むメールを検索。メルマガや EC サイトのドメインは除外
- **LLM によるメール解析**: Ollama（`gemma2:9b`）で企業名・イベント種別・開始/終了日時・提出物かどうかを JSON で抽出
- **Google カレンダー登録**: 抽出したイベントを `primary` カレンダーに登録
- **選考ステータスの自動更新**: 「お見送り通知」「内定通知」を検出すると企業のステータスを自動で変更
- **デスクトップ UI（Flet）**
  - カンバン: 企業を「選考中 / 結果待ち / 結果 / お見送り」の 4 レーンで管理
  - ダッシュボード: 直近の予定と提出物 TODO（締切までの残り時間付き）を表示
  - カレンダー: 月 / 週 / 日表示、予定の手動追加
  - 企業詳細: イベント履歴のタイムライン、マイページ URL・ログイン情報の保存とコピー

処理済みのメールには Gmail で `Agent` ラベルが付き、次回の通常同期では対象外になります。

## 構成

| ファイル | 役割 |
| --- | --- |
| [app.py](app.py) | Flet 製のデスクトップ UI（エントリポイント） |
| [main.py](main.py) | Gmail 取得 → LLM 解析 → カレンダー登録を行うエージェント本体 |
| [db_manager.py](db_manager.py) | SQLite（`job_tracker.db`）の操作。`companies` と `events` の 2 テーブル |

## 必要なもの

- Python 3.11 以上
- [Ollama](https://ollama.com/) と `gemma2:9b` モデル
- Gmail API と Google Calendar API を有効にした Google Cloud プロジェクト

## セットアップ

### 1. 依存パッケージのインストール

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pyperclip
```

> `pyperclip` は app.py で使っていますが、現在 requirements.txt に入っていないため個別にインストールしてください。

### 2. Ollama の準備

```bash
ollama pull gemma2:9b
ollama serve
```

`http://localhost:11434` で起動している必要があります。モデルや URL を変える場合は [main.py](main.py) の `OLLAMA_MODEL` / `OLLAMA_API_URL` を編集してください。

### 3. Google API の認証情報

1. Google Cloud Console でプロジェクトを作成し、**Gmail API** と **Google Calendar API** を有効化
2. OAuth 同意画面を設定し、自分のアカウントをテストユーザーに追加
3. 「OAuth クライアント ID（デスクトップアプリ）」を作成して JSON をダウンロード
4. プロジェクト直下に `credentials.json` という名前で置く

初回実行時にブラウザが開くので、Google アカウントでログインして許可してください。認証結果は `token.json` に保存されます。

使用するスコープ:

- `https://www.googleapis.com/auth/gmail.modify`（メール読み取りとラベル付与）
- `https://www.googleapis.com/auth/calendar.events`（予定の登録）

## 使い方

### UI から使う（通常）

```bash
python app.py
```

ヘッダーのボタンから同期します。

- **初回取込**: 既読・処理済みを問わず就活メールを最大 200 件取り込む
- **同期**: `Agent` ラベルが付いていない新着メールを最大 50 件取り込む

### コマンドラインから同期だけ行う

```bash
python main.py
```

UI を開かずに通常同期を 1 回実行します。

## データの保存先

- `job_tracker.db`: 企業・イベント・TODO を保存する SQLite データベース（初回起動時に自動作成）
- `credentials.json` / `token.json`: Google の認証情報

これらはすべて `.gitignore` に含まれており、リポジトリにはコミットされません。

> **注意**: マイページのログイン ID・パスワードは `job_tracker.db` に平文で保存されます。DB ファイルの取り扱いに気をつけてください。

## カスタマイズ

| 変更したいこと | 場所 |
| --- | --- |
| メール検索キーワード | [main.py](main.py) の `_KEYWORD_FILTER` |
| 除外するドメイン | [main.py](main.py) の `NEWSLETTER_DOMAINS` |
| LLM のプロンプト・イベント種別 | [main.py](main.py) の `OllamaAnalyzer.SYSTEM_PROMPT` |
| 提出物（TODO）判定キーワード | [db_manager.py](db_manager.py) の `TODO_KEYWORDS` |
| UI の配色 | [app.py](app.py) の `C` |

## トラブルシューティング

- **「Ollama に接続できません」**: `ollama serve` が起動しているか確認してください
- **認証エラーが出る**: `token.json` を削除して再実行し、もう一度ログインしてください
- **関係ないメールが登録される**: `NEWSLETTER_DOMAINS` に送信元ドメインを追加してください
