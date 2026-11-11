# -*- coding: utf-8 -*-
"""PLA Agent Pet: read-only, locally launched, Q-style activity viewer.

Loads user-supplied PNG animation frames without registering a PLA MCP tool.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import random
import re
import sqlite3
import sys
import time

from runtime.conversation_activity import automatic_conversation_summary

BASE = Path(__file__).resolve().parent
EVENT_NAMES = (
    "capability.before_invoke", "capability.succeeded",
    "capability.failed", "capability.gate_denied", "capability.policy_denied",
    "native_tool.started", "native_tool.succeeded", "native_tool.failed",
    "local_action.started", "local_action.succeeded", "local_action.failed",
)
_SAFE_PROGRAM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}\Z")
PROVIDER_NAMES = {
    "browser": "Browser", "drawio": "draw.io", "skill-library": "Skill Library",
    "core": "PLA Core", "runtime": "PLA Runtime", "windows": "Windows",
}
POLL_MS = 1200
MAX_ROWS = 4
ACTIVE_SECONDS = 20
QUIET_SECONDS = 90
PENDING_STALE_SECONDS = 900


def default_database() -> Path:
    configured = os.environ.get("PLA_PET_EVENT_DB", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "io.pla.desktop" / "state" / "events.sqlite3"


def pretty_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return "--:--:--"


def parse_timestamp(iso: str) -> float | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def last_call_label(last_event: float | None, now: float | None = None) -> str:
    """Short relative timing followed by stable local clock labels."""
    if last_event is None:
        return "尚无调用记录"
    now = time.time() if now is None else now
    age = max(0, int(now - last_event))
    if age < 20:
        return "刚刚调用"
    if age < 60:
        return "上次调用 · 不到1分钟前"
    if age < 15 * 60:
        return f"上次调用 · {age // 60}分钟前"
    recent = datetime.fromtimestamp(last_event)
    today = datetime.fromtimestamp(now).date()
    if recent.date() == today:
        when = f"今天 {recent:%H:%M}"
    elif recent.date() == today - timedelta(days=1):
        when = f"昨天 {recent:%H:%M}"
    else:
        when = recent.strftime("%m-%d %H:%M")
    return f"上次调用 · {when}"


def registered_dialogues_label(
    db: Path, runtime_started_at: str | None, *, now: datetime | None = None,
) -> str:
    """Count sessions from real MCP tool calls when the host provides IDs."""
    if not runtime_started_at:
        return "会话未知"
    summary = automatic_conversation_summary(db, runtime_started_at=runtime_started_at, now=now)
    if not summary["available"]:
        return "会话不可用"
    suffix = "+" if summary["truncated"] else ""
    label = f"活跃 {summary['identified_count']}{suffix}"
    if summary["unattributed_calls"]:
        label += f" · 未识别{summary['unattributed_calls']}"
    return label


def fit_text_width(text: str, measure, max_width: int) -> str:
    """Keep canvas labels one line without relying on character count."""
    if measure(text) <= max_width:
        return text
    while text and measure(text + "…") > max_width:
        text = text[:-1]
    return text + "…" if text else "…"


@dataclass(frozen=True)
class ToolCall:
    name: str
    time_label: str
    event_time: float
    started_sequence: int
    status: str  # running | succeeded | failed
    source: str = "native_mcp"
    provider: str | None = None
    program: str | None = None


def describe_call(call: ToolCall) -> tuple[str, str]:
    """User-facing identity without inferring a component from a process name."""
    if call.source == "capability":
        provider = call.provider or "组件能力"
        label = PROVIDER_NAMES.get(provider, provider)
        operation = call.name.removeprefix(provider + ".")
        return label, f"{operation} · 组件调用"
    if call.name == "run_process":
        return f"程序 · {call.program or '未知程序'}", "run_process · 本地执行"
    kind = "MCP工具" if call.source == "native_mcp" else "内部Action"
    return call.name, kind


@dataclass(frozen=True)
class Snapshot:
    calls: tuple[ToolCall, ...]
    mode: str     # active | pending | quiet | idle | disconnected
    message: str
    last_event: float | None


def _program_label(value: object) -> str | None:
    # The runtime writes a sanitized basename. Re-validate on read because event
    # databases can also contain legacy/third-party records.
    return value if isinstance(value, str) and _SAFE_PROGRAM.fullmatch(value) else None


def build_snapshot(rows: list[tuple], now: float, max_rows: int = MAX_ROWS) -> Snapshot:
    # Collapse start and terminal events. Never read tool arguments or file data.
    calls: dict[tuple[str, str], dict] = {}
    latest: float | None = None
    for row in reversed(rows):
        seq, timestamp, event_type, name, correlation_id, *extra = row
        source = extra[0] if len(extra) > 0 else "native_mcp"
        provider = extra[1] if len(extra) > 1 and source == "capability" else None
        raw_payload = extra[2] if len(extra) > 2 else None
        at = parse_timestamp(timestamp)
        if at is None:
            continue
        latest = max(latest or at, at)
        key = (source, str(correlation_id or f"seq-{seq}"))
        entry = calls.setdefault(key, {
            "name": str(name or "unknown")[:96],
            "first": at, "sequence": int(seq), "status": "running",
            "source": source, "provider": str(provider)[:64] if provider else None,
            "program": None,
        })
        if event_type.endswith((".started", ".before_invoke")):
            entry["first"], entry["sequence"] = at, int(seq)
            if entry["name"] == "run_process" and raw_payload:
                try:
                    payload = json.loads(raw_payload)
                    if isinstance(payload, dict):
                        entry["program"] = _program_label(payload.get("program"))
                except (TypeError, ValueError):
                    pass
        elif event_type.endswith((".succeeded",)):
            entry["status"] = "succeeded"
        elif event_type.endswith((".failed", ".gate_denied", ".policy_denied")):
            entry["status"] = "failed"
    picked = sorted(calls.values(), key=lambda x: x["sequence"], reverse=True)[:max_rows]
    records = tuple(
        ToolCall(x["name"], datetime.fromtimestamp(x["first"]).strftime("%H:%M:%S"),
                 x["first"], x["sequence"], x["status"], x["source"],
                 x["provider"], x["program"])
        for x in picked
    )
    if latest is None:
        return Snapshot((), "idle", "尚无本地工具活动", None)
    age = max(0.0, now - latest)
    # A stale older start must not conceal newer successful tool calls.
    waiting = bool(records and records[0].status == "running")
    if waiting and age < PENDING_STALE_SECONDS:
        mode, message = ("active", "调用尚未返回") if age < ACTIVE_SECONDS else ("pending", "可能仍在执行 · 等待返回")
    elif age <= ACTIVE_SECONDS:
        mode, message = "active", "最近有工具活动"
    elif age <= QUIET_SECONDS:
        mode, message = "quiet", "短时无新调用"
    else:
        mode, message = "idle", "暂无新调用 · AI状态未知"
    return Snapshot(records, mode, message, latest)


def fetch_rows(db: Path, limit: int = 250) -> list[tuple]:
    if not db.is_file():
        raise FileNotFoundError(str(db))
    # Read-only SQLite snapshot; no schema migrations or runtime writes.
    uri = db.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.3)
    try:
        connection.execute("PRAGMA query_only=ON")
        sql = """
            SELECT sequence, timestamp, event_type,
                   COALESCE(capability_id, subject) AS tool_name,
                   correlation_id,
                   CASE WHEN source = 'capability_broker' THEN 'capability' ELSE source END,
                   CASE WHEN source = 'capability_broker' THEN provider_id ELSE NULL END,
                   payload
            FROM runtime_events
            WHERE
                (source = 'native_mcp'
                 AND event_type IN ('native_tool.started','native_tool.succeeded','native_tool.failed'))
                OR
                (source = 'local_action'
                 AND event_type IN ('local_action.started','local_action.succeeded','local_action.failed'))
                OR
                (source = 'capability_broker'
                 AND subject NOT LIKE 'runtime.%'
                 AND subject NOT LIKE 'core.event_%'
                 AND subject NOT LIKE 'core.hook_%'
                 AND subject NOT LIKE 'core.gate_%'
                 AND event_type IN ('capability.before_invoke','capability.succeeded',
                                    'capability.failed','capability.gate_denied','capability.policy_denied'))
            ORDER BY sequence DESC
            LIMIT ?
        """
        return connection.execute(sql, (min(400, max(1, int(limit))),)).fetchall()
    finally:
        connection.close()


def read_snapshot(db: Path, now: float | None = None) -> Snapshot:
    try:
        return build_snapshot(fetch_rows(db), now if now is not None else time.time())
    except (FileNotFoundError, sqlite3.Error, OSError):
        return Snapshot((), "disconnected", "PLA事件源未连接或不可读取", None)


# Friendly speech belongs to the presentation layer. Event snapshots keep their
# diagnostic message so callers and tests can still inspect actual observations.
STATUS_PHRASES: dict[str, tuple[str, ...]] = {
    "active": (
        "又有新动作啦～",
        "正在忙活呢！",
        "工具又动起来啦",
    ),
    "pending": (
        "这一步还没回音～",
        "还在等结果呢…",
        "这活儿有点久呀",
    ),
    "quiet": (
        "咦，安静下来了",
        "好像暂时没动静～",
        "我看看还有没有新活儿",
    ),
    "idle": (
        "我先发会儿呆～",
        "暂时没新活儿啦",
        "没动静，我先歇会儿～",
        "有新动静再叫我！",
        "先摸一会儿鱼～",
    ),
    "failed": (
        "哎呀，这步没跑通",
        "好像出了点小状况",
        "这次没成功呢",
    ),
    "disconnected": (
        "咦，听不到PLA了",
        "和PLA走散了吗？",
        "信号好像断啦…",
    ),
}

IDLE_PHRASE_MIN_SECONDS = 30
IDLE_PHRASE_MAX_SECONDS = 60
RECENT_FAILURE_SECONDS = 8


class StatusPhraseController:
    """Keep work-state text steady; only idle chatter rotates on a slow timer.

    No AI conversation or completion state is inferred from absent tool events.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng if rng is not None else random.Random()
        self._mode: str | None = None
        self._last_event: float | None = None
        self._text = ""
        self._next_idle_phrase = float("inf")

    @staticmethod
    def _display_mode(snapshot: Snapshot, now: float) -> str:
        if (
            snapshot.mode == "active"
            and snapshot.last_event is not None
            and 0 <= now - snapshot.last_event <= RECENT_FAILURE_SECONDS
            and snapshot.calls
            and snapshot.calls[0].status == "failed"
        ):
            return "failed"
        return snapshot.mode

    def update(self, snapshot: Snapshot, now: float | None = None) -> tuple[str, str]:
        now = time.time() if now is None else now
        mode = self._display_mode(snapshot, now)
        changed = mode != self._mode or snapshot.last_event != self._last_event
        rotate_idle = mode == "idle" and now >= self._next_idle_phrase

        if changed or rotate_idle or not self._text:
            choices = STATUS_PHRASES.get(mode, STATUS_PHRASES["disconnected"])
            if self._mode == mode and len(choices) > 1:
                # New events and idle rotations should not repeat the previous line.
                choices = tuple(phrase for phrase in choices if phrase != self._text)
            self._text = self._rng.choice(choices)
            self._mode = mode
            self._last_event = snapshot.last_event
            self._next_idle_phrase = (
                now + self._rng.uniform(IDLE_PHRASE_MIN_SECONDS, IDLE_PHRASE_MAX_SECONDS)
                if mode == "idle" else float("inf")
            )
        return self._text, mode


def run_gui() -> None:
    import tkinter as tk
    from tkinter import font as tkfont

    root = tk.Tk()
    root.title("PLA Agent Pet")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    trans = "#01050c"
    bg = "#151f31"
    panel = "#202d43"
    fg = "#f2f6ff"
    muted = "#9bacbe"
    font = "Microsoft YaHei UI"
    # Restore a compact width while preserving the existing 200px Q character.
    width, height = 490, 246
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{width}x{height}+{max(0, screen_w-width-40)}+{max(0, screen_h-height-70)}")
    root.configure(bg=trans)
    try:
        root.wm_attributes("-transparentcolor", trans)
    except tk.TclError:
        root.configure(bg=bg)
    canvas = tk.Canvas(root, width=width, height=height, bg=trans, highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)

    def round_rect(x1, y1, x2, y2, r, fill, outline=""):
        ps = [x1+r,y1,x2-r,y1,x2,y1,x2,y1+r,x2,y2-r,x2,y2,x2-r,y2,x1+r,y2,x1,y2,x1,y2-r,x1,y1+r,x1,y1]
        return canvas.create_polygon(ps, smooth=True, splinesteps=16, fill=fill, outline=outline)

    # No separate title, branding card or decorative label below the pet.
    round_rect(203, 12, 483, 237, 16, bg, "#344257")
    round_rect(211, 51, 475, 210, 10, panel)

    db = default_database()
    runtime_started_at = os.environ.get("PLA_PET_RUNTIME_STARTED_AT", "").strip() or None
    frames = {}
    frame_idx = 0
    for mode, prefix, count in [
        ("active", "work", 6), ("pending", "waiting", 6),
        ("quiet", "review", 6), ("idle", "idle", 6),
        ("failed", "failed", 8), ("disconnected", "failed", 8),
    ]:
        seq = []
        for i in range(count):
            path = Path(os.environ["PLA_PET_FRAMES_DIR"]) / f"{prefix}_{i}.png"
            if path.is_file():
                seq.append(tk.PhotoImage(file=str(path)))
        frames[mode] = seq
    image_item = canvas.create_image(100, 110, anchor="center")
    current_mode = "idle"
    last_snapshot: Snapshot | None = None
    moved = {"start": None}

    def show_menu(event):
        menu = tk.Menu(root, tearoff=0)
        menu.add_command(label="刷新事件", command=refresh)
        menu.add_checkbutton(label="始终置顶", variable=top_var,
                             command=lambda: root.attributes("-topmost",top_var.get()))
        menu.add_separator()
        menu.add_command(label="退出桌宠", command=root.destroy)
        menu.tk_popup(event.x_root,event.y_root)

    top_var = tk.BooleanVar(value=True)
    def drag_start(e):
        moved["start"]=(e.x_root, e.y_root, root.winfo_x(),root.winfo_y())
    def drag_motion(e):
        if moved["start"]:
            sx,sy,px,py=moved["start"]
            root.geometry(f"+{px+e.x_root-sx}+{py+e.y_root-sy}")
    root.bind("<ButtonPress-1>", drag_start)
    root.bind("<B1-Motion>", drag_motion)
    root.bind("<ButtonRelease-1>", lambda e: moved.update(start=None))
    root.bind("<Button-3>", show_menu)
    root.bind("<Escape>",lambda e:root.destroy())

    # Two lines per action. The outcome symbol follows the title, not the panel edge.
    title_font = tkfont.Font(root=root, family=font, size=10, weight="bold")
    sub_font = tkfont.Font(root=root, family=font, size=8)
    header_font = tkfont.Font(root=root, family=font, size=10, weight="bold")
    status_text = canvas.create_text(218, 31, anchor="w", text="", font=header_font, fill=fg)
    status_dot = canvas.create_oval(462, 26, 470, 34, fill="#8599b1",outline="")
    detail = []
    for i in range(MAX_ROWS):
        y = 62 + i * 40
        if i:
            canvas.create_line(218,y-14,464,y-14,fill="#354359")
        left = canvas.create_text(218,y+6,anchor="w",text="",font=(font,9),fill=muted)
        title = canvas.create_text(276,y,anchor="w",text="",font=title_font,fill=fg)
        description = canvas.create_text(276,y+14,anchor="w",text="",font=sub_font,fill=muted)
        right = canvas.create_text(460,y+4,anchor="center",text="",font=(font,11,"bold"),fill="#66d7af")
        detail.append((left,title,description,right))
    footer=canvas.create_text(218,225,anchor="w",text="",font=(font,8),fill=muted)
    registered_font = tkfont.Font(root=root, family=font, size=8)
    registered = canvas.create_text(462,225,anchor="e",text="",font=registered_font,fill=muted)
    # Keep the pet narrow: hovering over a clipped label reveals its full name.
    tooltip = {"window": None}
    def hide_tooltip(_event=None):
        if tooltip["window"] is not None:
            tip = tooltip["window"]
            tooltip["window"] = None
            tip.destroy()
    def show_tooltip(index):
        hide_tooltip()
        if not last_snapshot or index >= len(last_snapshot.calls):
            return
        call = last_snapshot.calls[index]
        name, subtitle = describe_call(call)
        if title_font.measure(name) <= 161 and sub_font.measure(subtitle) <= 185:
            return
        popup = tk.Toplevel(root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        tk.Label(popup, text=name + "\n" + subtitle + "\n" + call.name,
                 bg="#202d43", fg="#f2f6ff", font=(font,9),
                 justify="left", padx=10, pady=8, wraplength=310).pack()
        popup.update_idletasks()
        x = min(root.winfo_screenwidth() - popup.winfo_width(),
                root.winfo_pointerx() + 14)
        y = min(root.winfo_screenheight() - popup.winfo_height(),
                root.winfo_pointery() + 22)
        popup.geometry(f"+{max(0,x)}+{max(0,y)}")
        tooltip["window"] = popup
    for index, widgets in enumerate(detail):
        for item in widgets[1:3]:
            canvas.tag_bind(item, "<Enter>", lambda _e, i=index: show_tooltip(i))
            canvas.tag_bind(item, "<Leave>", hide_tooltip)
    colors={"active":"#65d8a4","pending":"#f0bf6d","quiet":"#9ab3ff",
            "idle":"#899bb1","failed":"#f38181","disconnected":"#f38181"}
    phrases = StatusPhraseController()
    last_poll=0.0

    def refresh():
        nonlocal current_mode,last_snapshot,last_poll
        s=read_snapshot(db)
        last_snapshot=s
        last_poll=time.time()
        status_line, current_mode = phrases.update(s, now=last_poll)
        if current_mode in {"active", "pending", "failed"} and s.calls:
            current_title, _ = describe_call(s.calls[0])
            status_line += " · " + current_title[:16]
        canvas.itemconfigure(status_text,
                             text=fit_text_width(status_line, header_font.measure, 233),
                             fill=colors.get(current_mode,fg))
        canvas.itemconfigure(status_dot,fill=colors.get(current_mode,muted))
        for i, widgets in enumerate(detail):
            if i < len(s.calls):
                call=s.calls[i]
                title, subtitle = describe_call(call)
                title = fit_text_width(title, title_font.measure, 161)
                subtitle = fit_text_width(subtitle, sub_font.measure, 185)
                state={"running":"…","succeeded":"✓","failed":"×"}.get(call.status,"–")
                state_color={"running":"#f0bf6d","succeeded":"#67d8ad","failed":"#f38181"}.get(call.status,fg)
                # Keep the state glyph close to its text even for short labels.
                state_x = min(461, 276 + title_font.measure(title) + 14)
                canvas.coords(widgets[3], state_x, 62 + i * 40 + 4)
                canvas.itemconfigure(widgets[0],text=call.time_label)
                canvas.itemconfigure(widgets[1],text=title)
                canvas.itemconfigure(widgets[2],text=subtitle)
                canvas.itemconfigure(widgets[3],text=state,fill=state_color)
            else:
                for item in widgets: canvas.itemconfigure(item,text="")
        registered_text = registered_dialogues_label(db, runtime_started_at)
        canvas.itemconfigure(registered, text=registered_text)
        # Footer shares its existing line; do not enlarge the pet or overlap labels.
        free_width = max(36, 462 - registered_font.measure(registered_text) - 12 - 218)
        canvas.itemconfigure(footer, text=fit_text_width(
            last_call_label(s.last_event, last_poll), sub_font.measure, free_width))

    def tick():
        nonlocal frame_idx
        if time.time()-last_poll >= POLL_MS/1000:
            refresh()
        opts=frames.get(current_mode,[]) or frames.get("idle",[])
        if opts:
            canvas.itemconfigure(image_item,image=opts[frame_idx%len(opts)])
            frame_idx+=1
        root.after(180,tick)

    root.after(100,tick)
    root.mainloop()


if __name__ == "__main__":
    if "--check" in sys.argv:
        snapshot=read_snapshot(default_database())
        print(f"db={default_database()}")
        print(f"mode={snapshot.mode} message={snapshot.message}")
        for r in snapshot.calls:
            title, subtitle = describe_call(r)
            print(f"{r.time_label} | {title} | {subtitle} | {r.status}")
    else:
        try:
            run_gui()
        except Exception:
            import traceback
            error_file = BASE / 'PLA_AGENT_PET_ERROR.log'
            error_file.write_text(traceback.format_exc(), encoding='utf-8')
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, f'桌宠启动失败，请查看：\n{error_file}', 'PLA Agent Pet', 0x10)
            except Exception:
                pass
            raise
