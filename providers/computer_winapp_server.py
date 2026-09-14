from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from fastmcp import FastMCP


mcp = FastMCP("computer-winapp")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROVIDER_ID = "computer"
EXPECTED_WINAPP_VERSION = "0.5.0"
PACKAGE_DIR = (
    PROJECT_ROOT
    / ".provider_envs"
    / PROVIDER_ID
    / "node_modules"
    / "@microsoft"
    / "winappcli"
)
STATE_DIR = PROJECT_ROOT / "state" / "computer"
CACHE_DIR = STATE_DIR / "winapp-cache"
_TYPE_CHUNK_CHARS = 128
_INTERNAL_WINDOW_TITLES = {
    "PLA Computer Use Indicator",
    "__PLA_INTERNAL_COMPUTER_USE_INDICATOR__",
}

_SAFE_KEYS = {
    "enter",
    "return",
    "tab",
    "esc",
    "escape",
    "space",
    "backspace",
    "delete",
    "del",
    "insert",
    "home",
    "end",
    "pageup",
    "pgup",
    "pagedown",
    "pgdn",
    "up",
    "down",
    "left",
    "right",
}


def _bounded_text(value: str, label: str, *, max_length: int = 2048) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} must be non-empty")
    if len(cleaned) > max_length:
        raise ValueError(f"{label} exceeds {max_length} characters")
    return cleaned


def _resolve_winapp_launch() -> tuple[str, str, str]:
    node = shutil.which("node.exe") or shutil.which("node")
    if not node:
        raise RuntimeError("Node.js was not found on PATH")

    package_json = PACKAGE_DIR / "package.json"
    if not package_json.is_file():
        raise RuntimeError(
            "Reviewed winapp CLI provider environment is missing; "
            "run setup_providers.ps1"
        )

    try:
        metadata = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Invalid winapp CLI package metadata") from exc

    version = str(metadata.get("version") or "").strip()
    if version != EXPECTED_WINAPP_VERSION:
        raise RuntimeError(
            f"winapp CLI version drift: expected {EXPECTED_WINAPP_VERSION}, "
            f"found {version or '<missing>'}"
        )

    bin_meta = metadata.get("bin")
    if isinstance(bin_meta, str):
        relative_bin = bin_meta
    elif isinstance(bin_meta, dict):
        relative_bin = bin_meta.get("winapp")
        if not isinstance(relative_bin, str) or not relative_bin.strip():
            candidates = [
                value
                for value in bin_meta.values()
                if isinstance(value, str) and value.strip()
            ]
            if len(candidates) != 1:
                raise RuntimeError("winapp CLI package does not expose a unique bin")
            relative_bin = candidates[0]
    else:
        raise RuntimeError("winapp CLI package metadata has no bin entry")

    package_root = PACKAGE_DIR.resolve()
    script = (PACKAGE_DIR / relative_bin).resolve()
    try:
        script.relative_to(package_root)
    except ValueError as exc:
        raise RuntimeError("winapp CLI bin escapes reviewed package directory") from exc
    if not script.is_file():
        raise RuntimeError("winapp CLI bin declared by package metadata is missing")

    return str(Path(node).resolve()), str(script), version


def _target_args(
    app: str | None,
    hwnd: int | None,
    *,
    required: bool = True,
) -> list[str]:
    if app is not None and hwnd is not None:
        raise ValueError("Specify either app or hwnd, not both")
    if hwnd is not None:
        if isinstance(hwnd, bool) or not isinstance(hwnd, int) or hwnd <= 0:
            raise ValueError("hwnd must be a positive integer")
        return ["-w", str(hwnd)]
    if app is not None:
        return ["-a", _bounded_text(app, "app", max_length=512)]
    if required:
        raise ValueError("One of app or hwnd is required")
    return []


def _parse_json_output(stdout: str) -> Any:
    cleaned = stdout.strip()
    if not cleaned:
        return {}
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError("winapp CLI returned non-JSON output") from exc


def _is_internal_ui_record(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(
        value.get(field) in _INTERNAL_WINDOW_TITLES
        for field in ("title", "name")
    )


def _filter_internal_ui(value: Any) -> Any:
    if isinstance(value, list):
        return [
            _filter_internal_ui(item)
            for item in value
            if not _is_internal_ui_record(item)
        ]
    if isinstance(value, dict):
        if _is_internal_ui_record(value):
            return {}
        return {key: _filter_internal_ui(item) for key, item in value.items()}
    return value


def _run_ui(
    args: list[str],
    *,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    if not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 120:
        raise ValueError("timeout_seconds must be between 1 and 120")

    node, script, version = _resolve_winapp_launch()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["WINAPP_CLI_UPDATE_CHECK"] = "0"
    env["WINAPP_CLI_CACHE_DIRECTORY"] = str(CACHE_DIR)

    argv = [node, script, "ui", *args, "--json"]
    try:
        completed = subprocess.run(
            argv,
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("winapp UI operation timed out") from exc

    data: Any = None
    if completed.stdout.strip():
        data = _filter_internal_ui(_parse_json_output(completed.stdout))
    if completed.returncode != 0 and data is None:
        detail = completed.stderr.strip()[:4000] or "winapp UI operation failed"
        raise RuntimeError(detail)

    return {
        "status": "completed" if completed.returncode == 0 else "not_matched",
        "returncode": completed.returncode,
        "backend": "microsoft-winapp-cli",
        "backend_version": version,
        "result": data if data is not None else {},
    }


def _window_records(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list):
        values = result
    elif isinstance(result, dict) and isinstance(result.get("windows"), list):
        values = result["windows"]
    else:
        values = []
    return [
        item
        for item in values
        if isinstance(item, dict) and not _is_internal_ui_record(item)
    ]


def _resolve_activation_hwnd(
    app: str | None,
    hwnd: int | None,
) -> int:
    _target_args(app, hwnd)
    if hwnd is not None:
        return hwnd

    clean_app = _bounded_text(app, "app", max_length=512)
    listed = _run_ui(["list-windows", "-a", clean_app])
    candidates = [
        item
        for item in _window_records(listed.get("result"))
        if isinstance(item.get("hwnd"), int) and item["hwnd"] > 0
    ]
    if not candidates:
        raise RuntimeError(f"No visible top-level window matched app: {clean_app}")
    if len(candidates) != 1:
        handles = ", ".join(str(item["hwnd"]) for item in candidates[:8])
        raise RuntimeError(
            "Window activation is ambiguous; provide a specific hwnd. "
            f"Matched HWNDs: {handles}"
        )
    return int(candidates[0]["hwnd"])


def _user32() -> Any:
    if os.name != "nt":
        raise RuntimeError("Computer window activation is supported on Windows only")
    return ctypes.WinDLL("user32", use_last_error=True)


def _kernel32() -> Any:
    if os.name != "nt":
        raise RuntimeError("Computer window activation is supported on Windows only")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _foreground_hwnd() -> int:
    user32 = _user32()
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    value = user32.GetForegroundWindow()
    return int(value or 0)


def _activate_hwnd_win32(hwnd: int) -> bool:
    user32 = _user32()
    kernel32 = _kernel32()

    user32.IsWindow.argtypes = [ctypes.c_void_p]
    user32.IsWindow.restype = ctypes.c_int
    user32.IsIconic.argtypes = [ctypes.c_void_p]
    user32.IsIconic.restype = ctypes.c_int
    user32.ShowWindowAsync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.ShowWindowAsync.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
    user32.AttachThreadInput.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int]
    user32.AttachThreadInput.restype = ctypes.c_int
    user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
    user32.BringWindowToTop.restype = ctypes.c_int
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.SetForegroundWindow.restype = ctypes.c_int
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = ctypes.c_ulong

    handle = ctypes.c_void_p(hwnd)
    if not user32.IsWindow(handle):
        raise ValueError(f"hwnd does not identify a live window: {hwnd}")
    if _foreground_hwnd() == hwnd:
        return True

    # Restore minimized windows, but never synthesize mouse/keyboard input or
    # change arbitrary window geometry.
    if user32.IsIconic(handle):
        user32.ShowWindowAsync(handle, 9)  # SW_RESTORE

    current_thread = int(kernel32.GetCurrentThreadId())
    foreground = _foreground_hwnd()
    foreground_thread = int(
        user32.GetWindowThreadProcessId(ctypes.c_void_p(foreground), None)
    ) if foreground else 0
    target_thread = int(user32.GetWindowThreadProcessId(handle, None))

    attached: list[int] = []
    for thread_id in dict.fromkeys((foreground_thread, target_thread)):
        if thread_id and thread_id != current_thread:
            if user32.AttachThreadInput(current_thread, thread_id, 1):
                attached.append(thread_id)

    try:
        user32.BringWindowToTop(handle)
        user32.SetForegroundWindow(handle)
    finally:
        for thread_id in reversed(attached):
            user32.AttachThreadInput(current_thread, thread_id, 0)

    for _ in range(10):
        if _foreground_hwnd() == hwnd:
            return True
        time.sleep(0.05)
    return False


def _observe_window(hwnd: int) -> dict[str, Any] | None:
    for _ in range(5):
        listed = _run_ui(["list-windows"])
        for item in _window_records(listed.get("result")):
            if item.get("hwnd") == hwnd:
                if item.get("isForeground") is True:
                    return item
                break
        time.sleep(0.05)
    return None


def _managed_png_path(output_path: str) -> Path:
    raw = _bounded_text(output_path, "output_path", max_length=4096)
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError("output_path must be an absolute broker-managed path")
    resolved = path.resolve()
    workspace_root = Path(
        os.environ.get("AGENT_WORKSPACE", str(PROJECT_ROOT / "workspace"))
    ).resolve()
    managed_root = (workspace_root / ".capability_io").resolve()
    try:
        resolved.relative_to(managed_root)
    except ValueError as exc:
        raise ValueError(
            "output_path must be managed under the PLA Artifact Plane"
        ) from exc
    if resolved.suffix.casefold() != ".png":
        raise ValueError("output_path must end in .png")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


@mcp.tool
def backend_status() -> dict[str, Any]:
    """Report whether the reviewed Microsoft winapp CLI backend is ready."""
    try:
        _node, _script, version = _resolve_winapp_launch()
    except Exception as exc:
        return {
            "status": "not_ready",
            "backend": "microsoft-winapp-cli",
            "expected_version": EXPECTED_WINAPP_VERSION,
            "message": str(exc),
        }
    return {
        "status": "ready",
        "backend": "microsoft-winapp-cli",
        "version": version,
        "expected_version": EXPECTED_WINAPP_VERSION,
    }


@mcp.tool
def list_windows(
    app: str | None = None,
    show_hidden: bool = False,
) -> dict[str, Any]:
    """List visible top-level Windows UI windows, optionally filtered by app."""
    args = ["list-windows"]
    args.extend(_target_args(app, None, required=False))
    if show_hidden:
        args.append("--show-hidden")
    return _run_ui(args)


@mcp.tool
def inspect(
    app: str | None = None,
    hwnd: int | None = None,
    selector: str | None = None,
    depth: int = 3,
    interactive: bool = False,
    hide_disabled: bool = False,
    hide_offscreen: bool = False,
) -> dict[str, Any]:
    """Inspect a Windows application's UI Automation tree."""
    if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 12:
        raise ValueError("depth must be between 1 and 12")
    args = ["inspect"]
    if selector is not None:
        args.append(_bounded_text(selector, "selector"))
    args.extend(_target_args(app, hwnd))
    args.extend(["--depth", str(depth)])
    if interactive:
        args.append("--interactive")
    if hide_disabled:
        args.append("--hide-disabled")
    if hide_offscreen:
        args.append("--hide-offscreen")
    return _run_ui(args)


@mcp.tool
def search(
    query: str,
    app: str | None = None,
    hwnd: int | None = None,
    max_results: int = 20,
) -> dict[str, Any]:
    """Search a Windows application's UI Automation tree."""
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 100:
        raise ValueError("max_results must be between 1 and 100")
    args = ["search", _bounded_text(query, "query")]
    args.extend(["--max", str(max_results)])
    args.extend(_target_args(app, hwnd))
    return _run_ui(args)


@mcp.tool
def get_property(
    selector: str,
    property_name: str | None = None,
    app: str | None = None,
    hwnd: int | None = None,
) -> dict[str, Any]:
    """Read UI Automation properties for one semantic selector."""
    args = ["get-property", _bounded_text(selector, "selector")]
    if property_name is not None:
        args.extend(["-p", _bounded_text(property_name, "property_name", max_length=128)])
    args.extend(_target_args(app, hwnd))
    return _run_ui(args)


@mcp.tool
def get_value(
    selector: str,
    app: str | None = None,
    hwnd: int | None = None,
) -> dict[str, Any]:
    """Read the best available semantic value/text for one UI element."""
    args = ["get-value", _bounded_text(selector, "selector")]
    args.extend(_target_args(app, hwnd))
    return _run_ui(args)


@mcp.tool
def get_focused(
    app: str | None = None,
    hwnd: int | None = None,
) -> dict[str, Any]:
    """Return the UI element that currently owns keyboard focus."""
    args = ["get-focused"]
    args.extend(_target_args(app, hwnd))
    return _run_ui(args)


@mcp.tool
def invoke(
    selector: str,
    app: str | None = None,
    hwnd: int | None = None,
) -> dict[str, Any]:
    """Activate an element through UIA patterns without raw coordinate input."""
    args = ["invoke", _bounded_text(selector, "selector")]
    args.extend(_target_args(app, hwnd))
    return _run_ui(args)


@mcp.tool
def set_value(
    selector: str,
    value: str,
    app: str | None = None,
    hwnd: int | None = None,
) -> dict[str, Any]:
    """Set an editable element through Value/RangeValue/IAccessible semantics."""
    clean_value = value if isinstance(value, str) else None
    if clean_value is None:
        raise TypeError("value must be a string")
    if len(clean_value) > 20000:
        raise ValueError("value exceeds 20000 characters")
    args = ["set-value", _bounded_text(selector, "selector"), clean_value]
    args.extend(_target_args(app, hwnd))
    return _run_ui(args)


@mcp.tool
def focus(
    selector: str,
    app: str | None = None,
    hwnd: int | None = None,
) -> dict[str, Any]:
    """Move keyboard focus to a semantic UI element."""
    args = ["focus", _bounded_text(selector, "selector")]
    args.extend(_target_args(app, hwnd))
    return _run_ui(args)


@mcp.tool
def activate(
    app: str | None = None,
    hwnd: int | None = None,
) -> dict[str, Any]:
    """Bring one top-level window to the Windows foreground and verify it."""
    target_hwnd = _resolve_activation_hwnd(app, hwnd)
    before_hwnd = _foreground_hwnd()
    if not _activate_hwnd_win32(target_hwnd):
        raise RuntimeError(
            "focus_not_foreground: Windows did not grant foreground activation "
            f"for hwnd {target_hwnd}"
        )

    observation = _observe_window(target_hwnd)
    if observation is None:
        raise RuntimeError(
            "foreground_observation_failed: target reached the Windows foreground "
            "but winapp did not confirm isForeground=true"
        )

    _node, _script, version = _resolve_winapp_launch()
    return {
        "status": "completed",
        "returncode": 0,
        "backend": "microsoft-winapp-cli",
        "backend_version": version,
        "result": {
            "hwnd": target_hwnd,
            "previousForegroundHwnd": before_hwnd,
            "isForeground": True,
            "activationBackend": "windows-user32",
            "window": observation,
        },
    }


@mcp.tool
def scroll_into_view(
    selector: str,
    app: str | None = None,
    hwnd: int | None = None,
) -> dict[str, Any]:
    """Bring an element into view using UI Automation semantics."""
    args = ["scroll-into-view", _bounded_text(selector, "selector")]
    args.extend(_target_args(app, hwnd))
    return _run_ui(args)


@mcp.tool
def scroll(
    selector: str,
    direction: str | None = None,
    to: str | None = None,
    app: str | None = None,
    hwnd: int | None = None,
) -> dict[str, Any]:
    """Scroll via UIA ScrollPattern; raw mouse-wheel injection is not exposed."""
    if (direction is None) == (to is None):
        raise ValueError("Specify exactly one of direction or to")
    args = ["scroll", _bounded_text(selector, "selector")]
    if direction is not None:
        direction = direction.casefold()
        if direction not in {"up", "down", "left", "right"}:
            raise ValueError("direction must be up/down/left/right")
        args.extend(["--direction", direction])
    if to is not None:
        to = to.casefold()
        if to not in {"top", "bottom"}:
            raise ValueError("to must be top or bottom")
        args.extend(["--to", to])
    args.extend(_target_args(app, hwnd))
    return _run_ui(args)


@mcp.tool
def wait_for(
    selector: str,
    app: str | None = None,
    hwnd: int | None = None,
    value: str | None = None,
    property_name: str | None = None,
    gone: bool = False,
    contains: bool = False,
    timeout_ms: int = 5000,
) -> dict[str, Any]:
    """Wait for a semantic element/state with a bounded timeout."""
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 100 <= timeout_ms <= 30000:
        raise ValueError("timeout_ms must be between 100 and 30000")
    if gone and (value is not None or property_name is not None or contains):
        raise ValueError("gone cannot be combined with value/property/contains")
    args = ["wait-for", _bounded_text(selector, "selector")]
    args.extend(_target_args(app, hwnd))
    args.extend(["--timeout", str(timeout_ms)])
    if value is not None:
        if not isinstance(value, str) or len(value) > 20000:
            raise ValueError("value must be a string up to 20000 characters")
        args.extend(["--value", value])
    if property_name is not None:
        args.extend(["--property", _bounded_text(property_name, "property_name", max_length=128)])
    if gone:
        args.append("--gone")
    if contains:
        args.append("--contains")
    return _run_ui(args, timeout_seconds=min(120, max(5, timeout_ms // 1000 + 5)))


@mcp.tool
def screenshot(
    output_path: str,
    app: str | None = None,
    hwnd: int | None = None,
    selector: str | None = None,
    capture_screen: bool = False,
) -> dict[str, Any]:
    """Capture a managed PNG for visual verification."""
    path = _managed_png_path(output_path)
    args = ["screenshot"]
    if selector is not None:
        args.append(_bounded_text(selector, "selector"))
    args.extend(_target_args(app, hwnd))
    args.extend(["--output", str(path)])
    if capture_screen:
        args.append("--capture-screen")
    result = _run_ui(args, timeout_seconds=45)
    result["managed_output"] = str(path)
    return result


@mcp.tool
def click(
    selector: str,
    app: str | None = None,
    hwnd: int | None = None,
    double: bool = False,
    right: bool = False,
) -> dict[str, Any]:
    """Fallback semantic click using real mouse input; arbitrary coordinates are not accepted."""
    if double and right:
        raise ValueError("double and right cannot both be true")
    args = ["click", _bounded_text(selector, "selector")]
    args.extend(_target_args(app, hwnd))
    if double:
        args.append("--double")
    if right:
        args.append("--right")
    return _run_ui(args)


@mcp.tool
def type_text(
    selector: str,
    text: str,
    app: str | None = None,
    hwnd: int | None = None,
) -> dict[str, Any]:
    """Fallback literal typing through targeted SendInput; system shortcuts are not exposed."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not text:
        raise ValueError("text must be non-empty")
    if len(text) > 4096:
        raise ValueError("text exceeds 4096 characters")

    target = _bounded_text(selector, "selector")
    target_args = _target_args(app, hwnd)
    chunks = [
        text[index:index + _TYPE_CHUNK_CHARS]
        for index in range(0, len(text), _TYPE_CHUNK_CHARS)
    ]
    last: dict[str, Any] | None = None
    for index, chunk in enumerate(chunks, start=1):
        args = [
            "send-keys",
            chunk,
            "--verbatim",
            "--target",
            target,
            "--via",
            "send-input",
        ]
        args.extend(target_args)
        last = _run_ui(args)
        if last.get("returncode") != 0:
            last["chunk_index"] = index
            return last
        if index != len(chunks):
            time.sleep(0.075)

    return {
        "status": "completed",
        "returncode": 0,
        "backend": last["backend"] if last else "microsoft-winapp-cli",
        "backend_version": last["backend_version"] if last else EXPECTED_WINAPP_VERSION,
        "result": {
            "target": target,
            "via": "send-input",
            "characters": len(text),
            "chunk_count": len(chunks),
        },
    }


@mcp.tool
def press_key(
    key: str,
    app: str | None = None,
    hwnd: int | None = None,
    selector: str | None = None,
) -> dict[str, Any]:
    """Press one allow-listed navigation/edit key; modifier/system shortcuts are intentionally excluded."""
    clean_key = _bounded_text(key, "key", max_length=32).casefold()
    if clean_key not in _SAFE_KEYS:
        raise ValueError("key is not in the v1.4 safe-key allowlist")
    args = ["send-keys", clean_key, "--via", "send-input"]
    if selector is not None:
        args.extend(["--target", _bounded_text(selector, "selector")])
    args.extend(_target_args(app, hwnd))
    return _run_ui(args)


if __name__ == "__main__":
    mcp.run()
