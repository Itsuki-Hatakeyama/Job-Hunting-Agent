"""
db_manager.py
就活管理アプリ フェーズ2 - SQLiteデータベース管理モジュール

テーブル:
  - companies : 企業マスター（選考ステータス・マイページ情報）
  - events    : 選考イベント履歴（LLM解析結果・メール原文）
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
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
    company_id     INTEGER,
    event_type     TEXT NOT NULL,
    start_datetime TEXT NOT NULL,
    end_datetime   TEXT,
    mail_summary   TEXT,
    mail_raw       TEXT,
    is_completed   INTEGER DEFAULT 0,
    is_todo        INTEGER DEFAULT 0,
    gmail_msg_id   TEXT,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);
"""

# 有効なステータス一覧
VALID_STATUSES = ["選考中", "結果待ち", "結果", "お見送り"]

# ─────────────────────────────────────────────
# 提出物判定キーワード（キーワード判定層）
# ─────────────────────────────────────────────
TODO_KEYWORDS = [
    "ES", "エントリーシート", "締切", "提出", "エントリー",
    "応募書類", "履歴書", "適性検査", "Webテスト", "SPI",
    "締め切り", "期限", "提出期限", "書類選考",
]


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
        conn.executescript(DDL)

        # 既存テーブルへのマイグレーション（カラムが無ければ追加）
        for col, definition in [
            ("gmail_msg_id", "TEXT"),
            ("is_todo",      "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE events ADD COLUMN {col} {definition}")
            except Exception:
                pass

        # ユニークインデックス
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
    company_id: Optional[int],
    event_type: str,
    start_datetime: str,
    end_datetime: Optional[str] = None,
    mail_summary: Optional[str] = None,
    mail_raw: Optional[str] = None,
    gmail_msg_id: Optional[str] = None,
    is_todo: int = 0,
) -> int:
    """選考イベントを1件挿入する。gmail_msg_id が既存の場合は挿入をスキップして既存 id を返す。Returns: event_id"""
    try:
        start_dt = datetime.fromisoformat(start_datetime)
        is_completed = 1 if start_dt < datetime.now(tz=start_dt.tzinfo) else 0
    except ValueError:
        is_completed = 0

    with get_connection() as conn:
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
                 mail_summary, mail_raw, is_completed, is_todo, gmail_msg_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (company_id, event_type, start_datetime, end_datetime,
             mail_summary, mail_raw, is_completed, is_todo, gmail_msg_id),
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
    ただし is_todo=1（提出物）は手動チェックのみで完了とするため除外する。
    Returns: 更新件数
    """
    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with get_connection() as conn:
        cur = conn.execute(
            """UPDATE events SET is_completed = 1
               WHERE start_datetime < ? AND is_completed = 0 AND is_todo = 0""",
            (now_str,),
        )
        return cur.rowcount


# ─────────────────────────────────────────────
# ダッシュボード用クエリ
# ─────────────────────────────────────────────

def get_upcoming_events(days: int = 7) -> list[dict]:
    """
    今日から指定日数以内の未完了イベント（is_todo=0）を日付昇順で返す。
    企業名も JOIN して付与する。
    """
    now_str  = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    end_str  = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT e.*, c.name AS company_name
            FROM events e
            LEFT JOIN companies c ON e.company_id = c.id
            WHERE e.start_datetime >= ?
              AND e.start_datetime <= ?
              AND e.is_todo = 0
            ORDER BY e.start_datetime ASC
            """,
            (now_str, end_str),
        ).fetchall()
        return [dict(r) for r in rows]


def get_pending_todos() -> list[dict]:
    """
    未完了の提出物タスク（is_todo=1, is_completed=0）を締切が近い順で返す。
    企業名も JOIN して付与する。
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT e.*, c.name AS company_name
            FROM events e
            LEFT JOIN companies c ON e.company_id = c.id
            WHERE e.is_todo = 1 AND e.is_completed = 0
            ORDER BY e.start_datetime ASC
            """,
        ).fetchall()
        return [dict(r) for r in rows]


def complete_todo(event_id: int) -> None:
    """提出物タスクを手動で完了済みにする（is_completed = 1）。"""
    with get_connection() as conn:
        conn.execute(
            "UPDATE events SET is_completed = 1 WHERE id = ? AND is_todo = 1",
            (event_id,),
        )


def insert_manual_todo(
    company_name: Optional[str],
    title: str,
    deadline_datetime: str,
) -> dict:
    """
    手動で提出物タスクを追加する。
    company_name が指定されれば companies を upsert して紐付ける。
    Returns: {"company_id": int|None, "event_id": int}
    """
    company_id = None
    if company_name and company_name.strip():
        company_id = upsert_company(company_name.strip())

    event_id = insert_event(
        company_id=company_id,
        event_type=title,
        start_datetime=deadline_datetime,
        is_todo=1,
    )
    return {"company_id": company_id, "event_id": event_id}


# ─────────────────────────────────────────────
# カレンダー用クエリ
# ─────────────────────────────────────────────

def get_events_for_month(year: int, month: int) -> list[dict]:
    """
    指定年月のイベントを全て取得する（is_todo 問わず）。
    企業名も JOIN して付与する。
    """
    start_str = f"{year:04d}-{month:02d}-01T00:00:00"
    # 月末算出
    if month == 12:
        end_str = f"{year+1:04d}-01-01T00:00:00"
    else:
        end_str = f"{year:04d}-{month+1:02d}-01T00:00:00"

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT e.*, c.name AS company_name
            FROM events e
            LEFT JOIN companies c ON e.company_id = c.id
            WHERE e.start_datetime >= ?
              AND e.start_datetime <  ?
            ORDER BY e.start_datetime ASC
            """,
            (start_str, end_str),
        ).fetchall()
        return [dict(r) for r in rows]


def get_events_for_week(year: int, month: int, day: int) -> list[dict]:
    """
    指定日を含む週（月曜始まり）のイベントを取得する。
    企業名も JOIN して付与する。
    """
    from datetime import date, timedelta
    d         = date(year, month, day)
    week_start = d - timedelta(days=d.weekday())   # 月曜
    week_end   = week_start + timedelta(days=7)

    start_str = week_start.strftime("%Y-%m-%dT00:00:00")
    end_str   = week_end.strftime("%Y-%m-%dT00:00:00")

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT e.*, c.name AS company_name
            FROM events e
            LEFT JOIN companies c ON e.company_id = c.id
            WHERE e.start_datetime >= ?
              AND e.start_datetime <  ?
            ORDER BY e.start_datetime ASC
            """,
            (start_str, end_str),
        ).fetchall()
        return [dict(r) for r in rows]


def get_events_for_day(year: int, month: int, day: int) -> list[dict]:
    """
    指定日のイベントを取得する。企業名も JOIN して付与する。
    """
    start_str = f"{year:04d}-{month:02d}-{day:02d}T00:00:00"
    end_str   = f"{year:04d}-{month:02d}-{day:02d}T23:59:59"

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT e.*, c.name AS company_name
            FROM events e
            LEFT JOIN companies c ON e.company_id = c.id
            WHERE e.start_datetime >= ?
              AND e.start_datetime <= ?
            ORDER BY e.start_datetime ASC
            """,
            (start_str, end_str),
        ).fetchall()
        return [dict(r) for r in rows]


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
    is_todo: int = 0,
) -> dict:
    """
    フェーズ1のLLM解析結果を受け取り、companies と events に保存する。
    面接系のイベント種別が検知された場合、企業ステータスを「結果待ち」に自動更新する。

    Returns: {"company_id": int, "event_id": int, "status_updated": bool}
    """
    INTERVIEW_KEYWORDS = ["面接", "interview"]

    company_id = upsert_company(company_name)

    event_id = insert_event(
        company_id=company_id,
        event_type=event_type,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        mail_summary=mail_summary,
        mail_raw=mail_raw,
        gmail_msg_id=gmail_msg_id,
        is_todo=is_todo,
    )

    status_updated = False
    if any(kw in event_type for kw in INTERVIEW_KEYWORDS):
        current = get_company(company_id)
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

    _demo = [
        {
            "company": "株式会社テックコーポ",
            "event_type": "1次面接",
            "start": "2026-06-01T13:00:00+09:00",
            "end":   "2026-06-01T14:00:00+09:00",
            "summary": "1次面接のご案内。オンライン（Zoom）にて実施。",
            "raw": "件名: 【重要】1次面接のご案内\n...\nZoomのURLは別途お送りします。",
            "is_todo": 0,
        },
        {
            "company": "株式会社テックコーポ",
            "event_type": "会社説明会",
            "start": "2026-05-10T10:00:00+09:00",
            "end":   "2026-05-10T12:00:00+09:00",
            "summary": "会社説明会（録画あり）に参加済み。",
            "raw": "件名: 説明会のご案内\n...",
            "is_todo": 0,
        },
        {
            "company": "グローバル商事株式会社",
            "event_type": "ES締切",
            "start": "2026-06-10T23:59:00+09:00",
            "end": None,
            "summary": "エントリーシート提出締切。マイページより提出。",
            "raw": "件名: エントリーシート提出のお願い\n...",
            "is_todo": 1,
        },
        {
            "company": "未来ベンチャーズ",
            "event_type": "最終面接",
            "start": "2026-06-10T15:00:00+09:00",
            "end":   "2026-06-10T16:30:00+09:00",
            "summary": "最終面接のご案内（対面・本社にて）。",
            "raw": "件名: 最終面接ご案内\n...",
            "is_todo": 0,
        },
        {
            "company": "創造エンジニアリング",
            "event_type": "内定通知",
            "start": "2026-05-15T09:00:00+09:00",
            "end": None,
            "summary": "内定のご連絡。承諾期限は6月末。",
            "raw": "件名: 採用内定のご通知\n...",
            "is_todo": 0,
        },
        {
            "company": "フューチャー株式会社",
            "event_type": "Webテスト締切",
            "start": "2026-06-09T23:59:00+09:00",
            "end": None,
            "summary": "SPI Webテストの受験期限。",
            "raw": "件名: Webテスト受験のご案内\n...",
            "is_todo": 1,
        },
        {
            "company": "グリーンテック",
            "event_type": "2次面接",
            "start": "2026-06-11T11:00:00+09:00",
            "end":   "2026-06-11T12:00:00+09:00",
            "summary": "2次面接のご案内。",
            "raw": "件名: 2次面接のご案内\n...",
            "is_todo": 0,
        },
        {
            "company": "合同説明会",
            "event_type": "合同企業説明会",
            "start": "2026-06-08T13:00:00+09:00",
            "end":   "2026-06-08T17:00:00+09:00",
            "summary": "○○大学主催 合同企業説明会。",
            "raw": "件名: 合同説明会のご案内\n...",
            "is_todo": 0,
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
            is_todo=d["is_todo"],
        )
        print(f"  [SEED] {d['company']} / {d['event_type']} → {result}")

    all_cos = get_all_companies()
    for c in all_cos:
        if c["name"] == "創造エンジニアリング":
            update_company_status(c["id"], "結果")
        elif c["name"] == "グローバル商事株式会社":
            update_company_status(c["id"], "選考中")

    refresh_completed_flags()
    print("\n[DB] シードデータの投入が完了しました。")