# -*- coding: utf-8 -*-
"""PLA Agent Pet: read-only, locally launched, Q-style activity viewer.

Loads user-supplied PNG animation frames without registering a PLA MCP tool.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import random
import sqlite3
import sys
import time

BASE = Path(__file__).resolve().parent
EVENT_NAMES = ("capability.before_invoke", "capability.succeeded", "capability.failed",
               "native_tool.started", "native_tool.succeeded", "native_tool.failed")
POLL_MS = 1200
MAX_ROWS = 5
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


@dataclass(frozen=True)
class ToolCall:
    name: str
    time_label: str
    event_time: float
    started_sequence: int
    status: str  # running | succeeded | failed


@dataclass(frozen=True)
class Snapshot:
    calls: tuple[ToolCall, ...]
    mode: str     # active | pending | quiet | idle | disconnected
    message: str
    last_event: float | None


def build_snapshot(rows: list[tuple], now: float, max_rows: int = MAX_ROWS) -> Snapshot:
    # Collapse "before" and terminal events into one row per invocation.
    calls: dict[str, dict] = {}
    latest: float | None = None
    for seq, timestamp, event_type, capability_id, correlation_id in reversed(rows):
        at = parse_timestamp(timestamp)
        if at is None:
            continue
        latest = max(latest or at, at)
        key = str(correlation_id or f"seq-{seq}")
        entry = calls.setdefault(key, {
            "name": str(capability_id or "unknown")[:96],
            "first": at, "sequence": int(seq), "status": "running",
        })
        if event_type in ("capability.before_invoke", "native_tool.started"):
            entry["first"] = at
            entry["sequence"] = int(seq)
        elif event_type in ("capability.succeeded", "native_tool.succeeded"):
            entry["status"] = "succeeded"
        elif event_type in ("capability.failed", "native_tool.failed"):
            entry["status"] = "failed"
    picked = sorted(calls.values(), key=lambda x: x["sequence"], reverse=True)[:max_rows]
    records = tuple(ToolCall(x["name"], datetime.fromtimestamp(x["first"]).strftime("%H:%M:%S"),
                             x["first"], x["sequence"], x["status"]) for x in picked)
    if latest is None:
        return Snapshot((), "idle", "尚无Capability事件", None)
    age = max(0.0, now - latest)
    waiting = any(v["status"] == "running" for v in calls.values())
    if waiting and age < PENDING_STALE_SECONDS:
        # No terminal event does not imply AI has stopped.
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
    # SQLite read-only URI: no changes to PLA's event database, schema or WAL.
    uri = db.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.3)
    try:
        connection.execute("PRAGMA query_only=ON")
        sql = """
            SELECT sequence, timestamp, event_type,
                   COALESCE(capability_id, subject) AS tool_name, correlation_id
            FROM runtime_events
            WHERE
                (source = 'native_mcp'
                 AND event_type IN ('native_tool.started','native_tool.succeeded','native_tool.failed'))
                OR
                (source = 'capability_broker'
                 AND capability_id != 'runtime.provider_catalog'
                 AND event_type IN ('capability.before_invoke','capability.succeeded','capability.failed'))
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
    # Compact floating panel: keep the original-size character, compress its log.
    width, height = 480, 224
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
    round_rect(203, 12, 471, 212, 16, bg, "#344257")
    round_rect(211, 51, 463, 183, 10, panel)

    db = default_database()
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

    # One compact status line, then five single-line tool records.
    status_text = canvas.create_text(218, 31, anchor="w", text="", font=(font,10,"bold"),fill=fg)
    status_dot = canvas.create_oval(447, 26, 455, 34, fill="#8599b1",outline="")
    detail = []
    for i in range(MAX_ROWS):
        y = 64 + i*25
        if i:
            canvas.create_line(219,y-12,451,y-12,fill="#354359")
        left = canvas.create_text(219,y,anchor="w",text="",font=(font,9),fill=muted)
        middle=canvas.create_text(282,y,anchor="w",text="",font=(font,10),fill=fg)
        right=canvas.create_text(450,y,anchor="e",text="",font=(font,11,"bold"),fill="#66d7af")
        detail.append((left,middle,right))
    footer=canvas.create_text(218,199,anchor="w",text="",font=(font,8),fill=muted)
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
        canvas.itemconfigure(status_text,text=status_line,fill=colors.get(current_mode,fg))
        canvas.itemconfigure(status_dot,fill=colors.get(current_mode,muted))
        for i, widgets in enumerate(detail):
            if i < len(s.calls):
                call=s.calls[i]
                name=call.name if len(call.name)<=19 else "…"+call.name[-18:]
                state={"running":"…","succeeded":"✓","failed":"×"}.get(call.status,"–")
                state_color={"running":"#f0bf6d","succeeded":"#67d8ad","failed":"#f38181"}.get(call.status,fg)
                canvas.itemconfigure(widgets[0],text=call.time_label)
                canvas.itemconfigure(widgets[1],text=name)
                canvas.itemconfigure(widgets[2],text=state,fill=state_color)
            else:
                for item in widgets: canvas.itemconfigure(item,text="")
        if s.last_event:
            diff=int(max(0,time.time()-s.last_event))
            ago=f"上次调用 {diff}秒前" if diff<3600 else f"上次调用 {diff//60}分钟前"
        else:
            ago="等待工具记录"
        canvas.itemconfigure(footer,text=ago)

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
            print(f"{r.time_label} | {r.name} | {r.status}")
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
