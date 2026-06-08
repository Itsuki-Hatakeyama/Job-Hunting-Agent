"""
app.py  ―  就活ダッシュボード（上段ダッシュボード追加版）
"""

import threading
import webbrowser
from datetime import datetime
import pyperclip

import flet as ft

import db_manager as db

# ─────────────────────────────────────────────
# テーマ（ライト・白背景）
# ─────────────────────────────────────────────

C = {
    "bg":           "#F5F6FA",
    "surface":      "#FFFFFF",
    "surface2":     "#F0F1F7",
    "card":         "#FFFFFF",
    "card_hover":   "#F0F1FF",
    "border":       "#E2E4EF",
    "border_dark":  "#C8CBDE",
    "accent":       "#5B52F0",
    "accent_light": "#EAE9FF",
    "green":        "#16A34A",
    "green_light":  "#DCFCE7",
    "amber":        "#D97706",
    "amber_light":  "#FEF3C7",
    "red":          "#DC2626",
    "red_light":    "#FEE2E2",
    "blue":         "#2563EB",
    "blue_light":   "#DBEAFE",
    "orange":       "#EA580C",
    "orange_light": "#FFEDD5",
    "purple":       "#7C3AED",
    "text_primary": "#1A1A2E",
    "text_sec":     "#5A5C7A",
    "text_muted":   "#9899B5",
    "lane_header": {
        "選考中":   "#5B52F0",
        "結果待ち": "#2563EB",
        "結果":     "#16A34A",
        "お見送り": "#9899B5",
    },
    "lane_bg": {
        "選考中":   "#F7F7FF",
        "結果待ち": "#F5F9FF",
        "結果":     "#F5FFF8",
        "お見送り": "#FAFAFA",
    },
    "lane_icon": {
        "選考中":   "📋",
        "結果待ち": "🗓️",
        "結果":     "🎉",
        "お見送り": "🌸",
    },
}

LANES = ["選考中", "結果待ち", "結果", "お見送り"]

# イベント種別 → 選考系 / 説明会系 の分類キーワード
SCHEDULE_KEYWORDS_SELECTION = ["面接", "選考", "ES", "締切", "Webテスト", "SPI", "適性", "インターン", "GD", "内定", "提出", "エントリー"]
SCHEDULE_KEYWORDS_BRIEFING  = ["説明会", "セミナー", "イベント", "合同", "就活イベント"]


# ─────────────────────────────────────────────
# Flet ショートハンド
# ─────────────────────────────────────────────

def pad(left=0, top=0, right=0, bottom=0):
    return ft.Padding(left=left, top=top, right=right, bottom=bottom)

def pad_all(v):
    return ft.Padding(left=v, top=v, right=v, bottom=v)

def pad_sym(h=0, v=0):
    return ft.Padding(left=h, top=v, right=h, bottom=v)

def mar(left=0, top=0, right=0, bottom=0):
    return ft.Margin(left=left, top=top, right=right, bottom=bottom)

def border_all(w, color):
    s = ft.BorderSide(w, color)
    return ft.Border(left=s, top=s, right=s, bottom=s)

def border_only(left=None, top=None, right=None, bottom=None):
    n = ft.BorderSide(0, "transparent")
    return ft.Border(left=left or n, top=top or n,
                     right=right or n, bottom=bottom or n)

def br_only(tl=0, tr=0, bl=0, br_=0):
    return ft.BorderRadius(top_left=tl, top_right=tr,
                           bottom_left=bl, bottom_right=br_)

def show_snack(page, message, color="#FFFFFF", bg="#1A1A2E"):
    sb = ft.SnackBar(
        content=ft.Text(message, color=color),
        bgcolor=bg,
        duration=2000,
        open=True,
    )
    page.overlay.append(sb)
    page.update()


# ─────────────────────────────────────────────
# 日時フォーマット
# ─────────────────────────────────────────────

def fmt_dt(iso_str):
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%m/%d %H:%M")
    except ValueError:
        return iso_str[:16]

def fmt_deadline_diff(iso_str):
    """締切まであと何日・何時間かを返す（例: 'あと2日', 'あと3時間'）。"""
    if not iso_str:
        return ""
    try:
        dt  = datetime.fromisoformat(iso_str)
        now = datetime.now(tz=dt.tzinfo)
        diff = dt - now
        total_seconds = diff.total_seconds()
        if total_seconds < 0:
            return "期限切れ"
        hours = total_seconds / 3600
        if hours < 24:
            return f"あと{int(hours)}時間"
        return f"あと{int(hours // 24)}日"
    except Exception:
        return ""

def get_next_event(events):
    future = [e for e in events if not e["is_completed"]]
    if future:
        return min(future, key=lambda e: e["start_datetime"])
    return events[-1] if events else None

def classify_event(event_type: str) -> str:
    """'selection'（選考系）か 'briefing'（説明会系）か返す。"""
    et = event_type or ""
    if any(kw in et for kw in SCHEDULE_KEYWORDS_SELECTION):
        return "selection"
    if any(kw in et for kw in SCHEDULE_KEYWORDS_BRIEFING):
        return "briefing"
    return "selection"  # デフォルトは選考系


# ─────────────────────────────────────────────
# ダッシュボード：スケジュールリスト行
# ─────────────────────────────────────────────

def build_schedule_row(ev):
    """直近スケジュールリストの1行を返す。"""
    kind   = classify_event(ev.get("event_type", ""))
    fg     = C["blue"]   if kind == "selection" else C["orange"]
    bg     = C["blue_light"] if kind == "selection" else C["orange_light"]
    icon   = "🔵" if kind == "selection" else "🟠"
    co     = ev.get("company_name") or "—"
    et     = ev.get("event_type", "")[:12]
    dt_str = fmt_dt(ev.get("start_datetime"))

    return ft.Container(
        content=ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Container(
                    content=ft.Text(icon, size=13),
                    width=22,
                ),
                ft.Container(
                    content=ft.Text(
                        et, size=11,
                        weight=ft.FontWeight.W_600,
                        color=fg,
                    ),
                    bgcolor=bg,
                    border_radius=6,
                    padding=pad_sym(h=7, v=3),
                ),
                ft.Text(
                    co,
                    size=12,
                    weight=ft.FontWeight.W_600,
                    color=C["text_primary"],
                    expand=True,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Text(dt_str, size=11, color=C["text_muted"]),
            ],
        ),
        bgcolor=C["surface"],
        border_radius=8,
        padding=pad_sym(h=12, v=8),
        margin=mar(bottom=4),
        border=border_all(1, C["border"]),
        animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
    )


# ─────────────────────────────────────────────
# ダッシュボード：提出物行
# ─────────────────────────────────────────────

def build_todo_row(ev, on_check):
    """提出物リストの1行を返す。チェックボックス付き。"""
    co     = ev.get("company_name") or "—"
    et     = ev.get("event_type", "")[:16]
    diff   = fmt_deadline_diff(ev.get("start_datetime"))
    dt_str = fmt_dt(ev.get("start_datetime"))

    # 24時間以内ならレッド、48時間以内ならアンバー
    try:
        dt  = datetime.fromisoformat(ev["start_datetime"])
        now = datetime.now(tz=dt.tzinfo)
        hours_left = (dt - now).total_seconds() / 3600
        if hours_left < 0:
            diff_color = C["text_muted"]
        elif hours_left < 24:
            diff_color = C["red"]
        elif hours_left < 48:
            diff_color = C["amber"]
        else:
            diff_color = C["green"]
    except Exception:
        diff_color = C["text_muted"]

    cb = ft.Checkbox(
        value=False,
        active_color=C["accent"],
        check_color=C["surface"],
        on_change=lambda e, eid=ev["id"]: on_check(eid),
    )

    return ft.Container(
        content=ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                cb,
                ft.Column(
                    spacing=1,
                    expand=True,
                    controls=[
                        ft.Text(
                            f"{co}  {et}",
                            size=12,
                            weight=ft.FontWeight.W_600,
                            color=C["text_primary"],
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Text(dt_str, size=10, color=C["text_muted"]),
                    ],
                ),
                ft.Text(diff, size=11, weight=ft.FontWeight.W_700, color=diff_color),
            ],
        ),
        bgcolor=C["surface"],
        border_radius=8,
        padding=pad_sym(h=12, v=8),
        margin=mar(bottom=4),
        border=border_all(1, C["border"]),
        animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
    )


# ─────────────────────────────────────────────
# ダッシュボード：手動タスク追加ダイアログ
# ─────────────────────────────────────────────

def build_add_todo_dialog(page, on_save):
    """提出物を手動追加するダイアログを page.overlay に追加して開く。"""
    co_field = ft.TextField(
        label="企業名（任意）",
        hint_text="例: 株式会社〇〇",
        border_color=C["border_dark"],
        focused_border_color=C["accent"],
        color=C["text_primary"],
        bgcolor=C["surface2"],
        text_size=12,
        border_radius=8,
        content_padding=pad_sym(h=12, v=10),
        label_style=ft.TextStyle(color=C["text_sec"], size=11),
    )
    title_field = ft.TextField(
        label="タスク名 *",
        hint_text="例: ES提出、Webテスト受験",
        border_color=C["border_dark"],
        focused_border_color=C["accent"],
        color=C["text_primary"],
        bgcolor=C["surface2"],
        text_size=12,
        border_radius=8,
        content_padding=pad_sym(h=12, v=10),
        label_style=ft.TextStyle(color=C["text_sec"], size=11),
    )
    date_field = ft.TextField(
        label="締切日時 *",
        hint_text="例: 2026-06-15T23:59:00",
        border_color=C["border_dark"],
        focused_border_color=C["accent"],
        color=C["text_primary"],
        bgcolor=C["surface2"],
        text_size=12,
        border_radius=8,
        content_padding=pad_sym(h=12, v=10),
        label_style=ft.TextStyle(color=C["text_sec"], size=11),
    )
    error_text = ft.Text("", color=C["red"], size=11)

    def handle_save(e):
        title    = title_field.value.strip()
        deadline = date_field.value.strip()
        if not title:
            error_text.value = "タスク名を入力してください。"
            error_text.update()
            return
        if not deadline:
            error_text.value = "締切日時を入力してください。"
            error_text.update()
            return
        try:
            datetime.fromisoformat(deadline)
        except ValueError:
            error_text.value = "日時形式が不正です（例: 2026-06-15T23:59:00）"
            error_text.update()
            return

        on_save(
            co_field.value.strip() or None,
            title,
            deadline,
        )
        dlg.open = False
        page.update()

    def handle_cancel(e):
        dlg.open = False
        page.update()

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("提出物・タスクを追加", size=15, weight=ft.FontWeight.W_700, color=C["text_primary"]),
        content=ft.Container(
            width=360,
            content=ft.Column(
                tight=True,
                spacing=10,
                controls=[
                    co_field,
                    title_field,
                    date_field,
                    error_text,
                ],
            ),
        ),
        actions=[
            ft.TextButton(
                "キャンセル",
                on_click=handle_cancel,
                style=ft.ButtonStyle(color=C["text_sec"]),
            ),
            ft.FilledButton(
                "追加",
                on_click=handle_save,
                style=ft.ButtonStyle(
                    bgcolor=C["accent"],
                    color=C["surface"],
                    shape=ft.RoundedRectangleBorder(radius=8),
                ),
            ),
        ],
        bgcolor=C["surface"],
    )
    page.overlay.append(dlg)
    dlg.open = True
    page.update()


# ─────────────────────────────────────────────
# ダッシュボード：上段2列パネル全体
# ─────────────────────────────────────────────

def build_dashboard_panel(page, on_todo_check, on_add_todo):
    """
    上段ダッシュボードを返す。
    左：タブ付きスケジュールリスト / 右：提出物・締切リスト
    折りたたみトグル付き（高さ 180px ↔ 0px）。
    """
    # ── データ取得 ──
    upcoming  = db.get_upcoming_events(days=7)
    todos     = db.get_pending_todos()

    # ── 選考系 / 説明会系に分類 ──
    selection_events = [e for e in upcoming if classify_event(e.get("event_type", "")) == "selection"]
    briefing_events  = [e for e in upcoming if classify_event(e.get("event_type", "")) == "briefing"]

    # ── スケジュールリスト ──
    def make_schedule_list(events):
        if not events:
            return ft.Container(
                content=ft.Text("予定なし", size=11, color=C["text_muted"]),
                padding=pad_sym(h=4, v=8),
            )
        return ft.Column(
            spacing=0,
            controls=[build_schedule_row(e) for e in events],
        )

    # 3つのコンテンツを事前生成
    content_all       = make_schedule_list(upcoming)
    content_selection = make_schedule_list(selection_events)
    content_briefing  = make_schedule_list(briefing_events)

    # タブ状態管理
    active_tab:  list = ["all"]
    sched_inner: list = [None]  # ListView の controls を差し替えるため参照保持

    sched_lv = ft.ListView(
        expand=True,
        spacing=0,
        padding=pad_all(0),
        controls=[content_all],
    )
    sched_inner[0] = sched_lv

    def make_tab_btn(label, key, count):
        is_active = active_tab[0] == key

        btn = ft.Container(
            content=ft.Row(
                spacing=4,
                tight=True,
                controls=[
                    ft.Text(
                        label,
                        size=11,
                        weight=ft.FontWeight.W_600 if is_active else ft.FontWeight.W_400,
                        color=C["accent"] if is_active else C["text_muted"],
                    ),
                    ft.Container(
                        content=ft.Text(str(count), size=9, weight=ft.FontWeight.W_700,
                                        color=C["surface"] if is_active else C["text_muted"]),
                        bgcolor=C["accent"] if is_active else C["surface2"],
                        border_radius=8,
                        padding=pad_sym(h=5, v=2),
                    ),
                ],
            ),
            padding=pad_sym(h=10, v=5),
            border_radius=6,
            bgcolor=C["accent_light"] if is_active else "transparent",
            on_click=lambda e, k=key: switch_tab(k),
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )
        return btn

    tab_row_ref: list = [None]

    def switch_tab(key):
        active_tab[0] = key
        if key == "all":
            sched_lv.controls = [make_schedule_list(upcoming)]
        elif key == "selection":
            sched_lv.controls = [make_schedule_list(selection_events)]
        else:
            sched_lv.controls = [make_schedule_list(briefing_events)]
        # タブ行を再描画
        tab_row_ref[0].controls = build_tab_row()
        tab_row_ref[0].update()
        sched_lv.update()

    def build_tab_row():
        return [
            make_tab_btn("全て",    "all",       len(upcoming)),
            make_tab_btn("選考系",  "selection", len(selection_events)),
            make_tab_btn("説明会系","briefing",  len(briefing_events)),
        ]

    tab_row = ft.Row(spacing=4, controls=build_tab_row())
    tab_row_ref[0] = tab_row

    schedule_panel = ft.Container(
        expand=True,
        bgcolor=C["surface"],
        border_radius=12,
        border=border_all(1, C["border"]),
        shadow=ft.BoxShadow(blur_radius=8, spread_radius=0, color="#00000010", offset=ft.Offset(0, 2)),
        content=ft.Column(
            spacing=0,
            controls=[
                # ヘッダー行
                ft.Container(
                    bgcolor=C["surface"],
                    border_radius=br_only(tl=12, tr=12),
                    border=border_only(bottom=ft.BorderSide(1, C["border"])),
                    padding=pad(left=14, top=10, right=10, bottom=8),
                    content=ft.Row(
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Text("📅", size=14),
                            ft.Container(width=6),
                            ft.Text("直近1週間", size=12,
                                    weight=ft.FontWeight.W_700, color=C["text_primary"]),
                            ft.Container(expand=True),
                            tab_row,
                        ],
                    ),
                ),
                # リスト本体
                ft.Container(
                    expand=True,
                    padding=pad_sym(h=10, v=8),
                    content=sched_lv,
                ),
            ],
        ),
    )

    # ── 提出物リスト ──
    def build_todo_list():
        pending = db.get_pending_todos()
        if not pending:
            return [ft.Container(
                content=ft.Text("未完了タスクなし", size=11, color=C["text_muted"]),
                padding=pad_sym(h=4, v=8),
            )]
        return [build_todo_row(ev, on_todo_check) for ev in pending]

    todo_lv = ft.ListView(
        expand=True,
        spacing=0,
        padding=pad_all(0),
        controls=build_todo_list(),
    )

    def refresh_todos():
        todo_lv.controls = build_todo_list()
        todo_lv.update()

    # on_todo_check を受けてリフレッシュするラッパーを page 側に登録
    # （呼び出し元から refresh_todos を利用できるよう panel に属性として付与）

    todo_panel = ft.Container(
        expand=True,
        bgcolor=C["surface"],
        border_radius=12,
        border=border_all(1, C["border"]),
        shadow=ft.BoxShadow(blur_radius=8, spread_radius=0, color="#00000010", offset=ft.Offset(0, 2)),
        content=ft.Column(
            spacing=0,
            controls=[
                # ヘッダー行
                ft.Container(
                    bgcolor=C["surface"],
                    border_radius=br_only(tl=12, tr=12),
                    border=border_only(bottom=ft.BorderSide(1, C["border"])),
                    padding=pad(left=14, top=10, right=10, bottom=8),
                    content=ft.Row(
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Text("📋", size=14),
                            ft.Container(width=6),
                            ft.Text("提出物・締切", size=12,
                                    weight=ft.FontWeight.W_700, color=C["text_primary"]),
                            ft.Container(expand=True),
                            # 手動追加ボタン
                            ft.Container(
                                content=ft.Row(
                                    spacing=3, tight=True,
                                    controls=[
                                        ft.Icon(ft.Icons.ADD_ROUNDED, size=13, color=C["accent"]),
                                        ft.Text("追加", size=10,
                                                weight=ft.FontWeight.W_600, color=C["accent"]),
                                    ],
                                ),
                                bgcolor=C["accent_light"],
                                border_radius=6,
                                padding=pad_sym(h=8, v=4),
                                on_click=lambda e: on_add_todo(),
                            ),
                        ],
                    ),
                ),
                # リスト本体
                ft.Container(
                    expand=True,
                    padding=pad_sym(h=10, v=8),
                    content=todo_lv,
                ),
            ],
        ),
    )

    # ── 折りたたみ制御 ──
    OPEN_HEIGHT  = 180
    CLOSE_HEIGHT = 0
    is_open: list = [True]

    toggle_icon = ft.Icon(ft.Icons.KEYBOARD_ARROW_UP_ROUNDED, size=16, color=C["text_muted"])

    inner_row = ft.Row(
        expand=True,
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        controls=[schedule_panel, todo_panel],
    )

    content_wrapper = ft.Container(
        height=OPEN_HEIGHT,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        animate=ft.Animation(320, ft.AnimationCurve.EASE_IN_OUT_CUBIC),
        content=inner_row,
    )

    def handle_toggle(e):
        if is_open[0]:
            content_wrapper.height = CLOSE_HEIGHT
            toggle_icon.name       = ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED
        else:
            content_wrapper.height = OPEN_HEIGHT
            toggle_icon.name       = ft.Icons.KEYBOARD_ARROW_UP_ROUNDED
        is_open[0] = not is_open[0]
        content_wrapper.update()
        toggle_icon.update()

    toggle_btn = ft.Container(
        content=ft.Row(
            spacing=4, tight=True,
            controls=[
                ft.Text("ダッシュボード", size=11,
                        weight=ft.FontWeight.W_600, color=C["text_muted"]),
                toggle_icon,
            ],
        ),
        on_click=handle_toggle,
        padding=pad_sym(h=6, v=3),
        border_radius=6,
    )

    outer = ft.Container(
        bgcolor=C["bg"],
        padding=pad(left=16, top=8, right=16, bottom=0),
        content=ft.Column(
            spacing=6,
            controls=[
                ft.Row(
                    controls=[ft.Container(expand=True), toggle_btn],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                content_wrapper,
            ],
        ),
    )

    # リフレッシュ関数を外から呼べるよう outer に持たせる
    outer.refresh_todos = refresh_todos
    return outer


# ─────────────────────────────────────────────
# 企業カード
# ─────────────────────────────────────────────

def build_company_card(company, on_click):
    events     = db.get_events_by_company(company["id"])
    next_ev    = get_next_event(events)
    lane_color = C["lane_header"].get(company["status"], C["accent"])
    badge_text = fmt_dt(next_ev["start_datetime"]) if next_ev else "予定なし"
    badge_type = next_ev["event_type"] if next_ev else ""

    if "締切" in badge_type:
        badge_fg, badge_bg = C["red"],    C["red_light"]
    elif "面接" in badge_type:
        badge_fg, badge_bg = C["accent"], C["accent_light"]
    elif "説明会" in badge_type:
        badge_fg, badge_bg = C["blue"],   C["blue_light"]
    elif "内定" in badge_type:
        badge_fg, badge_bg = C["green"],  C["green_light"]
    else:
        badge_fg, badge_bg = C["amber"],  C["amber_light"]

    inner = ft.Container(
        content=ft.Column(
            spacing=10,
            controls=[
                ft.Row(
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Container(width=4, height=40, bgcolor=lane_color, border_radius=4),
                        ft.Column(
                            spacing=3, expand=True,
                            controls=[
                                ft.Text(company["name"], size=14, weight=ft.FontWeight.W_700,
                                        color=C["text_primary"], max_lines=2,
                                        overflow=ft.TextOverflow.ELLIPSIS),
                                ft.Text(f"イベント {len(events)}件", size=11, color=C["text_muted"]),
                            ],
                        ),
                    ],
                ),
                ft.Row(
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Container(
                            content=ft.Text(badge_type[:10] if badge_type else "予定なし",
                                            size=10, weight=ft.FontWeight.W_600, color=badge_fg),
                            bgcolor=badge_bg, border_radius=6, padding=pad_sym(h=8, v=4),
                        ),
                        ft.Text(badge_text, size=11, color=C["text_sec"]),
                    ],
                ),
            ],
        ),
        bgcolor=C["card"],
        border_radius=12,
        padding=pad_all(16),
        margin=mar(bottom=10),
        shadow=ft.BoxShadow(blur_radius=8, spread_radius=0, color="#00000012", offset=ft.Offset(0, 2)),
        animate=ft.Animation(250, ft.AnimationCurve.EASE_OUT),
        border=border_all(1, C["border"]),
        on_click=lambda e: on_click(company),
    )

    def on_hover(e):
        if e.data == "true":
            inner.bgcolor = C["card_hover"]
            inner.border  = border_all(1.5, C["accent"])
            inner.shadow  = ft.BoxShadow(blur_radius=18, spread_radius=0,
                                         color="#5B52F022", offset=ft.Offset(0, 6))
        else:
            inner.bgcolor = C["card"]
            inner.border  = border_all(1, C["border"])
            inner.shadow  = ft.BoxShadow(blur_radius=8, spread_radius=0,
                                         color="#00000012", offset=ft.Offset(0, 2))
        inner.update()

    inner.on_hover = on_hover
    return inner


# ─────────────────────────────────────────────
# タイムライン
# ─────────────────────────────────────────────

def build_timeline(events):
    if not events:
        return ft.Text("イベント履歴がありません", color=C["text_muted"], size=12)

    items = []
    for i, ev in enumerate(events):
        is_done   = bool(ev["is_completed"])
        is_last   = i == len(events) - 1
        dot_color = C["text_muted"] if is_done else C["accent"]
        text_col  = C["text_muted"] if is_done else C["text_primary"]
        bg        = "transparent"   if is_done else C["accent_light"]

        items.append(
            ft.Row(
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    ft.Column(
                        spacing=0,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Container(width=12, height=12, bgcolor=dot_color,
                                         border_radius=6, margin=mar(top=3)),
                            ft.Container(width=2, height=36, bgcolor=C["border_dark"],
                                         visible=not is_last),
                        ],
                    ),
                    ft.Container(
                        expand=True, bgcolor=bg, border_radius=8,
                        padding=pad_sym(h=10, v=7), margin=mar(bottom=4),
                        content=ft.Column(
                            spacing=2,
                            controls=[
                                ft.Text(ev["event_type"], size=12,
                                        weight=ft.FontWeight.W_600, color=text_col),
                                ft.Text(fmt_dt(ev["start_datetime"]), size=11, color=C["text_muted"]),
                                *(
                                    [ft.Text(ev["mail_summary"], size=11, color=C["text_muted"],
                                             max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)]
                                    if ev.get("mail_summary") else []
                                ),
                            ],
                        ),
                    ),
                ],
            )
        )
    return ft.Column(controls=items, spacing=0)


# ─────────────────────────────────────────────
# セクション補助
# ─────────────────────────────────────────────

def _hdr(text):
    return ft.Container(
        content=ft.Text(text, size=10, weight=ft.FontWeight.W_700, color=C["text_muted"]),
        margin=mar(bottom=10, top=6),
    )

def _div():
    return ft.Container(height=1, bgcolor=C["border"], margin=mar(top=16, bottom=16))


# ─────────────────────────────────────────────
# 詳細パネル
# ─────────────────────────────────────────────

def build_detail_panel(company, on_status_change, on_close, page):
    events = db.get_events_by_company(company["id"])
    accent = C["lane_header"].get(company["status"], C["accent"])

    url_field = ft.TextField(
        value=company.get("mypage_url") or "",
        label="マイページ URL", hint_text="https://...",
        border_color=C["border_dark"], focused_border_color=C["accent"],
        color=C["text_primary"], bgcolor=C["surface2"], text_size=12,
        border_radius=8, content_padding=pad_sym(h=12, v=10),
        label_style=ft.TextStyle(color=C["text_sec"], size=11),
    )
    id_field = ft.TextField(
        value=company.get("login_id") or "",
        label="ログインID", hint_text="メールアドレス等",
        border_color=C["border_dark"], focused_border_color=C["accent"],
        color=C["text_primary"], bgcolor=C["surface2"], text_size=12,
        border_radius=8, content_padding=pad_sym(h=12, v=10),
        label_style=ft.TextStyle(color=C["text_sec"], size=11),
    )
    pw_field = ft.TextField(
        value=company.get("login_pw") or "",
        label="パスワード", password=True, can_reveal_password=True,
        border_color=C["border_dark"], focused_border_color=C["accent"],
        color=C["text_primary"], bgcolor=C["surface2"], text_size=12,
        border_radius=8, content_padding=pad_sym(h=12, v=10),
        label_style=ft.TextStyle(color=C["text_sec"], size=11),
    )

    def handle_login(e):
        url = url_field.value.strip()
        uid = id_field.value.strip()
        pwd = pw_field.value.strip()
        if url:
            webbrowser.open(url)
        pyperclip.copy(f"ID: {uid}\nPW: {pwd}")
        show_snack(page, "🔑 IDとPWをクリップボードにコピーしました",
                   color="white", bg=C["text_primary"])

    def handle_save(e):
        db.update_company_mypage(
            company["id"],
            mypage_url=url_field.value.strip() or None,
            login_id=id_field.value.strip()    or None,
            login_pw=pw_field.value.strip()    or None,
        )
        show_snack(page, "✅ 保存しました", color="white", bg=C["green"])

    status_chips = ft.Row(
        wrap=True, spacing=6, run_spacing=6,
        controls=[
            ft.Container(
                content=ft.Text(
                    s, size=11, weight=ft.FontWeight.W_600,
                    color=(C["surface"] if company["status"] == s
                           else C["lane_header"].get(s, C["accent"])),
                ),
                bgcolor=(C["lane_header"].get(s, C["accent"])
                         if company["status"] == s else C["surface2"]),
                border_radius=20,
                padding=pad_sym(h=14, v=8),
                border=border_all(
                    1.5,
                    C["lane_header"].get(s, C["accent"])
                    if company["status"] == s else C["border_dark"]
                ),
                on_click=lambda e, st=s: on_status_change(company["id"], st),
                animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
            )
            for s in LANES
        ],
    )

    mail_panels = [
        ft.ExpansionPanel(
            header=ft.ListTile(
                title=ft.Text(
                    f"{ev['event_type']}  {fmt_dt(ev['start_datetime'])}",
                    size=12, color=C["text_sec"],
                ),
                dense=True,
            ),
            content=ft.Container(
                content=ft.Text(ev["mail_raw"], size=11, color=C["text_sec"], selectable=True),
                padding=pad_all(12), bgcolor=C["surface2"],
                border_radius=br_only(bl=8, br_=8),
            ),
            bgcolor=C["surface"],
        )
        for ev in reversed(events) if ev.get("mail_raw")
    ]

    return ft.Container(
        expand=True, bgcolor=C["surface"],
        content=ft.Column(
            spacing=0,
            controls=[
                ft.Container(
                    bgcolor=C["surface"],
                    border=border_only(bottom=ft.BorderSide(1, C["border"])),
                    padding=pad(left=20, top=18, right=12, bottom=16),
                    content=ft.Row(
                        vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            ft.Column(
                                expand=True, spacing=6,
                                controls=[
                                    ft.Text(company["name"], size=18,
                                            weight=ft.FontWeight.W_800, color=C["text_primary"]),
                                    ft.Container(
                                        content=ft.Text(company["status"], size=11,
                                                        weight=ft.FontWeight.W_600, color=accent),
                                        bgcolor=C["accent_light"] if accent == C["accent"] else
                                                C["green_light"]  if accent == C["green"]  else
                                                C["blue_light"],
                                        border_radius=6,
                                        padding=pad_sym(h=10, v=4),
                                    ),
                                ],
                            ),
                            ft.IconButton(icon=ft.Icons.CLOSE_ROUNDED,
                                          icon_color=C["text_muted"], icon_size=20,
                                          on_click=lambda e: on_close()),
                        ],
                    ),
                ),
                ft.Container(
                    expand=True,
                    content=ft.ListView(
                        expand=True, spacing=0,
                        padding=pad_sym(h=20, v=16),
                        controls=[
                            _hdr("ステータス変更"),
                            status_chips,
                            _div(),
                            _hdr("🔐 マイページコックピット"),
                            url_field,
                            ft.Container(height=10),
                            id_field,
                            ft.Container(height=10),
                            pw_field,
                            ft.Container(height=14),
                            ft.Row(
                                spacing=8,
                                controls=[
                                    ft.FilledButton(
                                        "🚀 マイページへログイン",
                                        on_click=handle_login, expand=True,
                                        style=ft.ButtonStyle(
                                            bgcolor=C["accent"], color=C["surface"],
                                            shape=ft.RoundedRectangleBorder(radius=8),
                                            padding=pad_sym(h=12, v=12),
                                        ),
                                    ),
                                    ft.OutlinedButton(
                                        "保存", on_click=handle_save,
                                        style=ft.ButtonStyle(
                                            color=C["text_sec"],
                                            side=ft.BorderSide(1, C["border_dark"]),
                                            shape=ft.RoundedRectangleBorder(radius=8),
                                            padding=pad_sym(h=16, v=12),
                                        ),
                                    ),
                                ],
                            ),
                            _div(),
                            _hdr("📅 選考タイムライン"),
                            build_timeline(events),
                            *(
                                [
                                    _div(),
                                    _hdr("📧 メール原文"),
                                    ft.ExpansionPanelList(controls=mail_panels, elevation=0),
                                ]
                                if mail_panels else []
                            ),
                            ft.Container(height=30),
                        ],
                    ),
                ),
            ],
        ),
    )


# ─────────────────────────────────────────────
# メインアプリ
# ─────────────────────────────────────────────

def main(page: ft.Page):
    page.title      = "就活ダッシュボード"
    page.bgcolor    = C["bg"]
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding    = 0
    page.window.width       = 1500
    page.window.height      = 920
    page.window.min_width   = 1100
    page.window.min_height  = 650
    page.window.alignment   = ft.Alignment(0, 0)

    selected_id: list = [None]
    syncing:     list = [False]

    lane_cols     = {lane: ft.Column(spacing=0) for lane in LANES}
    dashboard_ref: list = [None]   # build_dashboard_panel の戻り値を保持

    # ── 詳細パネル ──
    detail_inner   = ft.Container(expand=True)
    detail_wrapper = ft.Container(
        width=0,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        animate=ft.Animation(400, ft.AnimationCurve.EASE_OUT_CUBIC),
        bgcolor=C["surface"],
        border=border_only(left=ft.BorderSide(1, C["border"])),
        shadow=ft.BoxShadow(blur_radius=30, spread_radius=0,
                            color="#00000018", offset=ft.Offset(-4, 0)),
        content=ft.Container(width=380, expand=True, content=detail_inner),
    )

    def refresh_lanes(keep=False):
        companies = db.get_all_companies()
        for lane in LANES:
            lane_cols[lane].controls.clear()
        for co in companies:
            s = co["status"] if co["status"] in LANES else "選考中"
            lane_cols[s].controls.append(build_company_card(co, on_card_click))
        if keep and selected_id[0]:
            co = db.get_company(selected_id[0])
            if co:
                _render_detail(co)
        page.update()

    def refresh_dashboard():
        """ダッシュボードパネルのデータをリフレッシュする。"""
        if dashboard_ref[0]:
            dashboard_ref[0].refresh_todos()

    def on_todo_check(event_id: int):
        """提出物チェックボックスが押された時の処理。"""
        db.complete_todo(event_id)
        refresh_dashboard()

    def on_add_todo():
        """手動追加ボタンが押された時の処理。"""
        def save_handler(company_name, title, deadline):
            db.insert_manual_todo(company_name, title, deadline)
            refresh_dashboard()
            show_snack(page, "✅ タスクを追加しました", color="white", bg=C["green"])
        build_add_todo_dialog(page, save_handler)

    def on_card_click(company):
        selected_id[0] = company["id"]
        _render_detail(company)
        detail_wrapper.width = 380
        page.update()

    def _render_detail(company):
        detail_inner.content = build_detail_panel(
            company,
            on_status_change=handle_status_change,
            on_close=close_detail,
            page=page,
        )

    def close_detail():
        selected_id[0] = None
        detail_wrapper.width = 0
        page.update()

    def handle_status_change(company_id, new_status):
        db.update_company_status(company_id, new_status)
        refresh_lanes(keep=True)

    # ── 同期 ──
    def handle_sync(e, initial_mode: bool = False):
        if syncing[0]:
            return
        syncing[0] = True
        label = "全メールを初回取込中..." if initial_mode else "Gmailを同期中..."
        show_snack(page, f"🔄 {label}", color="white", bg=C["accent"])

        def run():
            try:
                from main import JobScheduleAgent
                agent  = JobScheduleAgent(initial_mode=initial_mode)
                result = agent.run()
                db.refresh_completed_flags()
                msg = f"✅ 登録: {result['success']}件 / スキップ: {result['skip']}件"
                show_snack(page, msg, color="white", bg=C["green"])
            except Exception as ex:
                show_snack(page, f"❌ エラー: {ex}", color="white", bg=C["red"])
            finally:
                syncing[0] = False
                refresh_lanes(keep=True)
                refresh_dashboard()

        threading.Thread(target=run, daemon=True).start()

    # ── ヘッダー ──
    header = ft.Container(
        height=60, bgcolor=C["surface"],
        border=border_only(bottom=ft.BorderSide(1, C["border"])),
        padding=pad_sym(h=24, v=0),
        content=ft.Row(
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Container(
                            content=ft.Text("⚡", size=20),
                            bgcolor=C["accent_light"], border_radius=10, padding=pad_all(8),
                        ),
                        ft.Column(
                            spacing=1,
                            controls=[
                                ft.Text("就活ダッシュボード", size=16,
                                        weight=ft.FontWeight.W_800, color=C["text_primary"]),
                                ft.Text("Job Hunt Tracker", size=10, color=C["text_muted"]),
                            ],
                        ),
                    ],
                ),
                ft.Container(expand=True),
                ft.OutlinedButton(
                    content=ft.Row(
                        spacing=5, tight=True,
                        controls=[
                            ft.Icon(ft.Icons.DOWNLOAD_ROUNDED, size=15, color=C["accent"]),
                            ft.Text("初回取込", size=12, weight=ft.FontWeight.W_600, color=C["accent"]),
                        ],
                    ),
                    on_click=lambda e: handle_sync(e, initial_mode=True),
                    style=ft.ButtonStyle(
                        side=ft.BorderSide(1.5, C["accent"]),
                        shape=ft.RoundedRectangleBorder(radius=10),
                        padding=pad_sym(h=14, v=10),
                    ),
                ),
                ft.Container(width=8),
                ft.FilledButton(
                    content=ft.Row(
                        spacing=6, tight=True,
                        controls=[
                            ft.Icon(ft.Icons.SYNC_ROUNDED, size=16, color=C["surface"]),
                            ft.Text("同期", size=13, weight=ft.FontWeight.W_600, color=C["surface"]),
                        ],
                    ),
                    on_click=handle_sync,
                    style=ft.ButtonStyle(
                        bgcolor=C["accent"],
                        shape=ft.RoundedRectangleBorder(radius=10),
                        padding=pad_sym(h=18, v=10),
                    ),
                ),
            ],
        ),
    )

    # ── カンバンレーン ──
    def build_lane(lane_name):
        accent_col = C["lane_header"][lane_name]
        icon       = C["lane_icon"][lane_name]

        count_text = ft.Text(
            str(len(lane_cols[lane_name].controls)),
            size=11, weight=ft.FontWeight.W_700, color=accent_col,
        )
        count_badge = ft.Container(
            content=count_text,
            bgcolor=C["surface2"], border_radius=10,
            padding=pad_sym(h=10, v=4), border=border_all(1, C["border"]),
        )

        return ft.Container(
            expand=1,
            content=ft.Column(
                spacing=0, expand=True,
                controls=[
                    ft.Container(
                        bgcolor=C["surface"],
                        border_radius=br_only(tl=12, tr=12),
                        border=ft.Border(
                            left=ft.BorderSide(1, C["border"]),
                            right=ft.BorderSide(1, C["border"]),
                            top=ft.BorderSide(3, accent_col),
                            bottom=ft.BorderSide(1, C["border"]),
                        ),
                        padding=pad(left=16, top=14, right=12, bottom=12),
                        content=ft.Row(
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Text(icon, size=16),
                                ft.Text(lane_name, size=13, weight=ft.FontWeight.W_700,
                                        color=C["text_primary"]),
                                ft.Container(expand=True),
                                count_badge,
                            ],
                        ),
                    ),
                    ft.Container(
                        expand=True,
                        bgcolor=C["lane_bg"][lane_name],
                        border_radius=br_only(bl=12, br_=12),
                        border=ft.Border(
                            left=ft.BorderSide(1, C["border"]),
                            right=ft.BorderSide(1, C["border"]),
                            bottom=ft.BorderSide(1, C["border"]),
                        ),
                        content=ft.ListView(
                            controls=[lane_cols[lane_name]],
                            expand=True,
                            padding=pad_all(12),
                        ),
                    ),
                ],
            ),
            animate=ft.Animation(300, ft.AnimationCurve.EASE_OUT),
        )

    kanban = ft.Row(
        controls=[build_lane(l) for l in LANES],
        expand=True,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        spacing=10,
    )

    # ── ダッシュボードパネル生成 ──
    dashboard_panel = build_dashboard_panel(page, on_todo_check, on_add_todo)
    dashboard_ref[0] = dashboard_panel

    # ── 全体レイアウト ──
    page.add(
        ft.Column(
            expand=True, spacing=0,
            controls=[
                header,
                ft.Row(
                    expand=True, spacing=0,
                    vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                    controls=[
                        # 左：メインエリア（ダッシュボード＋カンバン）
                        ft.Container(
                            expand=True,
                            bgcolor=C["bg"],
                            content=ft.Column(
                                expand=True, spacing=0,
                                controls=[
                                    # 上段ダッシュボード
                                    dashboard_panel,
                                    # カンバン
                                    ft.Container(
                                        expand=True,
                                        padding=pad(left=16, top=8, right=16, bottom=16),
                                        content=kanban,
                                    ),
                                ],
                            ),
                        ),
                        # 右：詳細パネル
                        detail_wrapper,
                    ],
                ),
            ],
        )
    )

    refresh_lanes()


if __name__ == "__main__":
    db.init_db()
    ft.run(main)