"""
db_manager.py
就活管理アプリ フェーズ2 - SQLiteデータベース管理モジュール

テーブル:
  - companies : 企業マスター（選考ステータス・マイページ情報）
  - events    : 選考イベント履歴（LLM解析結果・メール原文）
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path("job_tracker.db")

# ─────────────────────────────────────────────
# DDL
# ─────────────────────────────────────────────

DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS companies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    status     TEXT NOT NULL DEFAULT '選考中',
    mypage_url TEXT,
    login_id   TEXT,
    login_pw   TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id     INTEGER NOT NULL,
    event_type     TEXT NOT NULL,
    start_datetime TEXT NOT NULL,
    end_datetime   TEXT,
    mail_summary   TEXT,
    mail_raw       TEXT,
    is_completed   INTEGER DEFAULT 0,
    gmail_msg_id   TEXT,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);
"""

# 有効なステータス一覧
VALID_STATUSES = ["選考中", "結果待ち", "結果", "お見送り"]


# ─────────────────────────────────────────────
# DB接続ヘルパー
# ─────────────────────────────────────────────

@contextmanager
def get_connection():
    """SQLite接続のコンテキストマネージャ。commit/rollbackを自動管理する。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """DBファイルを作成し、テーブルを初期化する（冪等）。既存DBにはカラム追加マイグレーションも行う。"""
    with get_connection() as conn:
        # テーブル作成（既存テーブルはスキップ）
        conn.executescript(DDL)
        # 既存テーブルへの gmail_msg_id カラム追加（既にあればエラーを無視）
        try:
            conn.execute("ALTER TABLE events ADD COLUMN gmail_msg_id TEXT")
        except Exception:
            pass
        # ユニークインデックス作成（カラム追加後に実行）
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_gmail_msg_id "
                "ON events(gmail_msg_id) WHERE gmail_msg_id IS NOT NULL"
            )
        except Exception:
            pass
    print(f"[DB] 初期化完了: {DB_PATH.resolve()}")


# ─────────────────────────────────────────────
# companies テーブル操作
# ─────────────────────────────────────────────

def upsert_company(name: str, status: str = "選考中") -> int:
    """
    企業名で検索し、存在しなければ INSERT、存在すれば id を返す。
    status は INSERT 時のみ適用（UPDATE はしない）。
    Returns: company_id
    """
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM companies WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO companies (name, status) VALUES (?, ?)",
            (name, status),
        )
        return cur.lastrowid


def get_all_companies() -> list[dict]:
    """全企業を取得する。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM companies ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_company(company_id: int) -> Optional[dict]:
    """company_id で企業を1件取得する。"""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
        return dict(row) if row else None


def update_company_status(company_id: int, status: str) -> None:
    """企業のステータスを更新する。"""
    if status not in VALID_STATUSES:
        raise ValueError(f"無効なステータス: {status}。有効値: {VALID_STATUSES}")
    with get_connection() as conn:
        conn.execute(
            "UPDATE companies SET status = ? WHERE id = ?",
            (status, company_id),
        )


def update_company_mypage(
    company_id: int,
    mypage_url: Optional[str] = None,
    login_id: Optional[str] = None,
    login_pw: Optional[str] = None,
) -> None:
    """マイページ情報を更新する。None の項目は変更しない。"""
    with get_connection() as conn:
        if mypage_url is not None:
            conn.execute("UPDATE companies SET mypage_url = ? WHERE id = ?", (mypage_url, company_id))
        if login_id is not None:
            conn.execute("UPDATE companies SET login_id = ? WHERE id = ?", (login_id, company_id))
        if login_pw is not None:
            conn.execute("UPDATE companies SET login_pw = ? WHERE id = ?", (login_pw, company_id))


def delete_company(company_id: int) -> None:
    """企業を削除する（関連 events も CASCADE で削除）。"""
    with get_connection() as conn:
        conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))


# ─────────────────────────────────────────────
# events テーブル操作
# ─────────────────────────────────────────────

def insert_event(
    company_id: int,
    event_type: str,
    start_datetime: str,
    end_datetime: Optional[str] = None,
    mail_summary: Optional[str] = None,
    mail_raw: Optional[str] = None,
    gmail_msg_id: Optional[str] = None,
) -> int:
    """選考イベントを1件挿入する。gmail_msg_id が既存の場合は挿入をスキップして既存 id を返す。Returns: event_id"""
    # 開始日時が過去か判定して is_completed をセット
    try:
        start_dt = datetime.fromisoformat(start_datetime)
        is_completed = 1 if start_dt < datetime.now(tz=start_dt.tzinfo) else 0
    except ValueError:
        is_completed = 0

    with get_connection() as conn:
        # Gmail メッセージ ID による重複チェック
        if gmail_msg_id:
            existing = conn.execute(
                "SELECT id FROM events WHERE gmail_msg_id = ?", (gmail_msg_id,)
            ).fetchone()
            if existing:
                return existing["id"]

        cur = conn.execute(
            """
            INSERT INTO events
                (company_id, event_type, start_datetime, end_datetime,
                 mail_summary, mail_raw, is_completed, gmail_msg_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (company_id, event_type, start_datetime, end_datetime,
             mail_summary, mail_raw, is_completed, gmail_msg_id),
        )
        return cur.lastrowid


def get_events_by_company(company_id: int) -> list[dict]:
    """企業に紐づく選考イベントを日付昇順で取得する。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE company_id = ? ORDER BY start_datetime ASC",
            (company_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def refresh_completed_flags() -> int:
    """
    現在日時より過去の start_datetime を持つイベントの is_completed を 1 に更新する。
    Returns: 更新件数
    """
    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE events SET is_completed = 1 WHERE start_datetime < ? AND is_completed = 0",
            (now_str,),
        )
        return cur.rowcount


# ─────────────────────────────────────────────
# フェーズ1連携用：メール解析結果をDBに書き込む
# ─────────────────────────────────────────────

def save_email_event(
    company_name: str,
    event_type: str,
    start_datetime: str,
    end_datetime: Optional[str] = None,
    mail_summary: Optional[str] = None,
    mail_raw: Optional[str] = None,
    gmail_msg_id: Optional[str] = None,
) -> dict:
    """
    フェーズ1のLLM解析結果を受け取り、companies と events に保存する。
    面接系のイベント種別が検知された場合、企業ステータスを「面接待ち」に自動更新する。

    Returns: {"company_id": int, "event_id": int, "status_updated": bool}
    """
    INTERVIEW_KEYWORDS = ["面接", "interview"]

    # 1. 企業を upsert
    company_id = upsert_company(company_name)

    # 2. イベントを追加
    event_id = insert_event(
        company_id=company_id,
        event_type=event_type,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        mail_summary=mail_summary,
        mail_raw=mail_raw,
        gmail_msg_id=gmail_msg_id,
    )

    # 3. 面接系キーワードが含まれていればステータスを自動更新
    status_updated = False
    if any(kw in event_type for kw in INTERVIEW_KEYWORDS):
        current = get_company(company_id)
        # 内定・お見送り済みの企業は上書きしない
        if current and current["status"] not in ("結果", "お見送り"):
            update_company_status(company_id, "結果待ち")
            status_updated = True

    return {
        "company_id": company_id,
        "event_id": event_id,
        "status_updated": status_updated,
    }


# ─────────────────────────────────────────────
# エントリポイント（単体実行でDB初期化）
# ─────────────────────────────────────────────

if __name__ == "__main__":
    init_db()

    # ─── デモ用シードデータ（初回のみ投入）───
    _demo = [
        {
            "company": "株式会社テックコーポ",
            "event_type": "1次面接",
            "start": "2026-06-01T13:00:00+09:00",
            "end":   "2026-06-01T14:00:00+09:00",
            "summary": "1次面接のご案内。オンライン（Zoom）にて実施。",
            "raw": "件名: 【重要】1次面接のご案内\n...\nZoomのURLは別途お送りします。",
        },
        {
            "company": "株式会社テックコーポ",
            "event_type": "会社説明会",
            "start": "2026-05-10T10:00:00+09:00",
            "end":   "2026-05-10T12:00:00+09:00",
            "summary": "会社説明会（録画あり）に参加済み。",
            "raw": "件名: 説明会のご案内\n...",
        },
        {
            "company": "グローバル商事株式会社",
            "event_type": "ES締切",
            "start": "2026-05-28T23:59:00+09:00",
            "end": None,
            "summary": "エントリーシート提出締切。マイページより提出。",
            "raw": "件名: エントリーシート提出のお願い\n...",
        },
        {
            "company": "未来ベンチャーズ",
            "event_type": "最終面接",
            "start": "2026-06-10T15:00:00+09:00",
            "end":   "2026-06-10T16:30:00+09:00",
            "summary": "最終面接のご案内（対面・本社にて）。",
            "raw": "件名: 最終面接ご案内\n...",
        },
        {
            "company": "創造エンジニアリング",
            "event_type": "内定通知",
            "start": "2026-05-15T09:00:00+09:00",
            "end": None,
            "summary": "内定のご連絡。承諾期限は6月末。",
            "raw": "件名: 採用内定のご通知\n...",
        },
    ]

    for d in _demo:
        result = save_email_event(
            company_name=d["company"],
            event_type=d["event_type"],
            start_datetime=d["start"],
            end_datetime=d["end"],
            mail_summary=d["summary"],
            mail_raw=d["raw"],
        )
        print(f"  [SEED] {d['company']} / {d['event_type']} → {result}")

    # 内定企業はステータスを手動で更新
    all_cos = get_all_companies()
    for c in all_cos:
        if c["name"] == "創造エンジニアリング":
            update_company_status(c["id"], "結果")
        elif c["name"] == "グローバル商事株式会社":
            update_company_status(c["id"], "選考中")

    refresh_completed_flags()
    print("\n[DB] シードデータの投入が完了しました。")
