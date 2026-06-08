"""
就活スケジュール自動登録エージェント（フェーズ1）
- Gmail APIで就活関連メールを取得
- OllamaのローカルLLMでメール本文を解析
- Google Calendar APIにイベントを登録
"""

import base64
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header as _decode_header
from pathlib import Path

import requests
import db_manager as db
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from pydantic import BaseModel, ValidationError

# ─────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────

CREDENTIALS_FILE = Path("credentials.json")
TOKEN_FILE = Path("token.json")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
]

_SUBJECT_FILTER = (
    "(subject:面接 OR subject:選考 OR subject:説明会 OR subject:インターン "
    "OR subject:ES OR subject:締切 OR subject:マイページ OR subject:内定 "
    "OR subject:エントリー OR subject:承諾 OR subject:採用)"
)
GMAIL_SEARCH_QUERY  = f"-label:就活-処理済み {_SUBJECT_FILTER}"
GMAIL_INITIAL_QUERY = _SUBJECT_FILTER

NEWSLETTER_DOMAINS = [
    "onecareer.jp",
    "techoffer.jp",
    "offerbox.jp",
    "rikunabi.com",
    "mynavi.jp",
    "jobrass.com",
    "dodaycareer.jp",
]

PROCESSED_LABEL_NAME = "就活-処理済み"

OLLAMA_API_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL   = "gemma2:9b"

DEFAULT_EVENT_DURATION_HOURS = 1
CALENDAR_ID          = "primary"
REQUEST_SLEEP_SECONDS = 0.1

# ─────────────────────────────────────────────
# ロガー設定
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# データモデル（Pydantic）
# ─────────────────────────────────────────────

class EventInfo(BaseModel):
    """LLMが抽出するイベント情報のスキーマ"""
    company_name:   str | None = None
    event_type:     str | None = None
    start_datetime: str | None = None
    end_datetime:   str | None = None
    is_todo:        bool       = False   # ← 追加：提出物タスクかどうか


# ─────────────────────────────────────────────
# 提出物判定ヘルパー
# ─────────────────────────────────────────────

def keyword_is_todo(subject: str, body: str) -> bool:
    """
    キーワード判定層。
    件名または本文冒頭500文字にTODOキーワードが含まれれば True を返す。
    """
    target = (subject + " " + body[:500]).lower()
    return any(kw.lower() in target for kw in db.TODO_KEYWORDS)


# ─────────────────────────────────────────────
# 認証モジュール
# ─────────────────────────────────────────────

def get_google_credentials() -> Credentials:
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("アクセストークンを更新しています...")
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"{CREDENTIALS_FILE} が見つかりません。"
                    "Google Cloud Console からダウンロードして配置してください。"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
            logger.info("認証に成功しました。")

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        logger.info(f"トークンを {TOKEN_FILE} に保存しました。")

    return creds


# ─────────────────────────────────────────────
# Gmailモジュール
# ─────────────────────────────────────────────

class GmailClient:
    def __init__(self, creds: Credentials):
        self.service = build("gmail", "v1", credentials=creds)
        self._processed_label_id: str | None = None

    def _get_or_create_label(self, label_name: str) -> str:
        labels_result = self.service.users().labels().list(userId="me").execute()
        for label in labels_result.get("labels", []):
            if label["name"] == label_name:
                return label["id"]
        new_label = self.service.users().labels().create(
            userId="me",
            body={"name": label_name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
        ).execute()
        logger.info(f"Gmailラベルを作成しました: '{label_name}' (ID: {new_label['id']})")
        return new_label["id"]

    def fetch_target_emails(self, query: str, max_results: int = 50) -> list[dict]:
        results = self.service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()
        messages = results.get("messages", [])
        if not messages:
            logger.info("条件に一致するメールは見つかりませんでした。")
            return []

        logger.info(f"{len(messages)} 件のメールをフィルタリング中...")
        email_data_list = []
        skipped = 0

        for msg_meta in messages:
            msg_id = msg_meta["id"]
            try:
                meta = self.service.users().messages().get(
                    userId="me", id=msg_id, format="metadata",
                    metadataHeaders=["From", "Subject"],
                ).execute()
                headers    = {h["name"]: h["value"] for h in meta["payload"]["headers"]}
                from_header = headers.get("From", "")
                subject_raw = headers.get("Subject", "(件名なし)")

                if self._is_newsletter(from_header):
                    logger.debug(f"メルマガのためスキップ: {from_header[:60]}")
                    skipped += 1
                    continue

                msg = self.service.users().messages().get(
                    userId="me", id=msg_id, format="raw"
                ).execute()
                raw_data  = base64.urlsafe_b64decode(msg["raw"])
                email_msg = message_from_bytes(raw_data)
                body      = self._extract_plain_text(email_msg)
                subject   = self._decode_subject(subject_raw)

                email_data_list.append({"id": msg_id, "subject": subject, "body": body})
                time.sleep(REQUEST_SLEEP_SECONDS)

            except HttpError as e:
                logger.error(f"メール取得エラー (ID: {msg_id}): {e}")

        logger.info(f"個社メール: {len(email_data_list)} 件 / メルマガスキップ: {skipped} 件")
        return email_data_list

    @staticmethod
    def _is_newsletter(from_header: str) -> bool:
        from_lower = from_header.lower()
        return any(f"@{domain}" in from_lower for domain in NEWSLETTER_DOMAINS)

    @staticmethod
    def _decode_subject(raw_value: str) -> str:
        parts  = _decode_header(raw_value)
        result = ""
        for part, enc in parts:
            if isinstance(part, bytes):
                result += part.decode(enc or "utf-8", errors="replace")
            else:
                result += str(part)
        return result

    def _extract_plain_text(self, email_msg) -> str:
        if email_msg.is_multipart():
            for part in email_msg.walk():
                content_type = part.get_content_type()
                disposition  = str(part.get("Content-Disposition", ""))
                if content_type == "text/plain" and "attachment" not in disposition:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        else:
            payload = email_msg.get_payload(decode=True)
            charset = email_msg.get_content_charset() or "utf-8"
            if payload:
                return payload.decode(charset, errors="replace")
        return ""

    def mark_as_processed(self, msg_id: str) -> None:
        if self._processed_label_id is None:
            self._processed_label_id = self._get_or_create_label(PROCESSED_LABEL_NAME)
        self.service.users().messages().modify(
            userId="me", id=msg_id,
            body={"removeLabelIds": ["UNREAD"], "addLabelIds": [self._processed_label_id]},
        ).execute()
        logger.info(f"メール (ID: {msg_id}) を既読・処理済みに更新しました。")


# ─────────────────────────────────────────────
# LLM解析モジュール
# ─────────────────────────────────────────────

class OllamaAnalyzer:
    """Ollama ローカルLLM を使ってメール本文を解析するクラス"""

    # ── is_todo フィールドを追加したシステムプロンプト ──
    SYSTEM_PROMPT = """あなたはメール本文を解析して、就職活動に関するイベント情報をJSONで抽出するアシスタントです。

以下のJSON形式のみで回答してください。それ以外のテキスト、説明文、マークダウンのコードブロック（```）は一切含めないでください。

{
  "company_name": "企業名（文字列、不明な場合はnull）",
  "event_type": "イベント種別（例: 1次面接, 2次面接, 最終面接, 会社説明会, ES締切, Webテスト締切, インターンシップ 等。不明な場合はnull）",
  "start_datetime": "開始日時または締切日時（ISO 8601形式 例: 2026-05-20T14:00:00+09:00。不明な場合はnull）",
  "end_datetime": "終了日時（ISO 8601形式。明記されていない場合はnull）",
  "is_todo": "このメールが【締切】【提出】【エントリー】【書類提出】【Webテスト受験】など、ユーザーが期限内にアクションを完了する必要がある「提出物・タスク」かどうか。該当する場合は true、面接や説明会など参加型イベントの場合は false"
}"""

    def __init__(self, api_url: str = OLLAMA_API_URL, model: str = OLLAMA_MODEL):
        self.api_url = api_url
        self.model   = model

    def analyze(self, subject: str, body: str) -> EventInfo | None:
        user_message = f"件名: {subject}\n\n本文:\n{body[:3000]}"

        payload = {
            "model":  self.model,
            "prompt": user_message,
            "system": self.SYSTEM_PROMPT,
            "stream": False,
            "format": "json",
        }

        try:
            response = requests.post(self.api_url, json=payload, timeout=120)
            response.raise_for_status()
            result   = response.json()
            raw_text = result.get("response", "")

            parsed_dict = json.loads(raw_text)
            event_info  = EventInfo(**parsed_dict)
            logger.info(f"LLM解析結果: {event_info.model_dump()}")
            return event_info

        except requests.exceptions.ConnectionError:
            logger.error("Ollamaに接続できません。Ollamaが起動しているか確認してください（ollama serve）。")
            return None
        except requests.exceptions.Timeout:
            logger.error("Ollamaへのリクエストがタイムアウトしました。")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"LLMの出力をJSONとしてパースできませんでした: {e}\n出力内容: {raw_text[:200]}")
            return None
        except ValidationError as e:
            logger.error(f"JSONのスキーマ検証に失敗しました: {e}")
            return None


# ─────────────────────────────────────────────
# Googleカレンダーモジュール
# ─────────────────────────────────────────────

class CalendarClient:
    JST = timezone(timedelta(hours=9))

    def __init__(self, creds: Credentials):
        self.service = build("calendar", "v3", credentials=creds)

    def create_event(self, event_info: EventInfo) -> str | None:
        if not event_info.company_name:
            logger.warning("企業名が抽出できなかったため、カレンダー登録をスキップします。")
            return None
        if not event_info.start_datetime:
            logger.warning("開始日時が抽出できなかったため、カレンダー登録をスキップします。")
            return None

        event_type_str = event_info.event_type or "日程"
        # 提出物タスクはタイトルに【締切】を付与してカレンダー上で区別しやすくする
        prefix = "【締切】" if event_info.is_todo else ""
        title  = f"{prefix}[{event_info.company_name}] {event_type_str}"

        try:
            start_dt = datetime.fromisoformat(event_info.start_datetime)
        except ValueError:
            logger.error(f"start_datetime のパースに失敗しました: {event_info.start_datetime}")
            return None

        if event_info.end_datetime:
            try:
                end_dt = datetime.fromisoformat(event_info.end_datetime)
            except ValueError:
                end_dt = start_dt + timedelta(hours=DEFAULT_EVENT_DURATION_HOURS)
        else:
            end_dt = start_dt + timedelta(hours=DEFAULT_EVENT_DURATION_HOURS)

        event_body = {
            "summary": title,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Tokyo"},
            "end":   {"dateTime": end_dt.isoformat(),   "timeZone": "Asia/Tokyo"},
        }

        try:
            created_event = self.service.events().insert(
                calendarId=CALENDAR_ID, body=event_body
            ).execute()
            event_id  = created_event.get("id")
            event_link = created_event.get("htmlLink", "")
            logger.info(f"カレンダーに登録しました: 「{title}」({start_dt.strftime('%Y-%m-%d %H:%M')}) -> {event_link}")
            return event_id
        except HttpError as e:
            logger.error(f"カレンダー登録エラー: {e}")
            return None


# ─────────────────────────────────────────────
# メインエージェント
# ─────────────────────────────────────────────

class JobScheduleAgent:
    def __init__(self, initial_mode: bool = False):
        logger.info("Google API 認証を開始します...")
        creds = get_google_credentials()
        self.gmail    = GmailClient(creds)
        self.analyzer = OllamaAnalyzer()
        self.calendar = CalendarClient(creds)
        self.initial_mode = initial_mode

    def run(self) -> dict:
        mode_label = "【初回取込】" if self.initial_mode else "【通常同期】"
        logger.info("=" * 50)
        logger.info(f"就活スケジュール自動登録エージェント 開始 {mode_label}")
        logger.info("=" * 50)

        query       = GMAIL_INITIAL_QUERY if self.initial_mode else GMAIL_SEARCH_QUERY
        max_results = 200 if self.initial_mode else 50
        emails      = self.gmail.fetch_target_emails(query, max_results=max_results)

        if not emails:
            logger.info("処理対象の個社メールがありませんでした。終了します。")
            return {"success": 0, "skip": 0, "error": 0}

        success_count = 0
        skip_count    = 0
        error_count   = 0

        for email_data in emails:
            msg_id  = email_data["id"]
            subject = email_data["subject"]
            body    = email_data["body"]

            logger.info(f"\n--- メール処理中: {subject} (ID: {msg_id}) ---")

            # ── 提出物判定：キーワード層 ──
            kw_todo = keyword_is_todo(subject, body)

            # ── LLM解析 ──
            event_info = self.analyzer.analyze(subject, body)
            if event_info is None:
                logger.warning(f"メール解析をスキップしました (ID: {msg_id})")
                error_count += 1
                self.gmail.mark_as_processed(msg_id)
                continue

            # ── 提出物判定：キーワード OR LLM の OR結合 ──
            final_is_todo = 1 if (kw_todo or event_info.is_todo) else 0
            logger.info(f"is_todo 判定: keyword={kw_todo} / llm={event_info.is_todo} → final={final_is_todo}")

            # ── カレンダー登録 ──
            event_id = self.calendar.create_event(event_info)

            if event_id:
                try:
                    db.save_email_event(
                        company_name=event_info.company_name,
                        event_type=event_info.event_type or "不明",
                        start_datetime=event_info.start_datetime,
                        end_datetime=event_info.end_datetime,
                        mail_summary=subject,
                        mail_raw=body,
                        gmail_msg_id=msg_id,
                        is_todo=final_is_todo,
                    )
                    logger.info(f"DBに保存しました: {event_info.company_name} (is_todo={final_is_todo})")
                except Exception as db_err:
                    logger.error(f"DB保存エラー: {db_err}")
                self.gmail.mark_as_processed(msg_id)
                success_count += 1
            else:
                if event_info.company_name and event_info.start_datetime:
                    try:
                        db.save_email_event(
                            company_name=event_info.company_name,
                            event_type=event_info.event_type or "不明",
                            start_datetime=event_info.start_datetime,
                            end_datetime=event_info.end_datetime,
                            mail_summary=subject,
                            mail_raw=body,
                            gmail_msg_id=msg_id,
                            is_todo=final_is_todo,
                        )
                    except Exception:
                        pass
                self.gmail.mark_as_processed(msg_id)
                skip_count += 1

        logger.info("\n" + "=" * 50)
        logger.info(f"処理完了 | 登録成功: {success_count} / スキップ: {skip_count} / エラー: {error_count}")
        logger.info("=" * 50)
        return {"success": success_count, "skip": skip_count, "error": error_count}


# ─────────────────────────────────────────────
# エントリポイント
# ─────────────────────────────────────────────

if __name__ == "__main__":
    agent = JobScheduleAgent()
    agent.run()