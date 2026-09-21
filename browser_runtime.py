"""Independent Browser Runtime for PLA v1.3.

PLA owns two fixed local processes:
1. a pinned Playwright MCP HTTP server that launches Microsoft Edge with a
   PLA-managed persistent profile;
2. a tiny independent keeper client that holds one MCP session open.

The keeper keeps Playwright MCP's shared browser context alive while PLA HTTP is
restarted. Browser actions still use Playwright's native browser connection, not
CDP, avoiding Windows connectOverCDP mouse-input reliability issues.

This module is a fixed supervisor, not an arbitrary process runner.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
STATE_DIR = PROJECT_ROOT / "state" / "browser_runtime"
STATE_PATH = STATE_DIR / "runtime.json"
KEEPER_READY_PATH = STATE_DIR / "keeper_ready.json"
MCP_LOG_PATH = STATE_DIR / "playwright-mcp.log"
KEEPER_LOG_PATH = STATE_DIR / "keeper.log"
KEEPER_SCRIPT = PROJECT_ROOT / "browser_session_keeper.py"
PROFILE_DIR = PROJECT_ROOT / ".browser_profiles" / "v13-default"
OUTPUT_DIR = PROJECT_ROOT / "state" / "browser"

HOST = "127.0.0.1"
MCP_PORT = 8931
MCP_ENDPOINT_URL = f"http://localhost:{MCP_PORT}/mcp"
PACKAGE = "@playwright/mcp@0.0.82"
PACKAGE_VERSION = "0.0.82"

_START_TIMEOUT_SECONDS = 60.0
_KEEPER_TIMEOUT_SECONDS = 30.0
_LOCK = RLock()

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_state() -> dict[str, Any]:
    return _read_json(STATE_PATH)


def _write_state(payload: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_suffix(".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, STATE_PATH)


def _remove_path(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _port_listening(port: int) -> bool:
    try:
        with socket.create_connection((HOST, port), timeout=0.25):
            return True
    except OSError:
        return False


def _process_creation_marker(pid: int) -> int | None:
    if not isinstance(pid, int) or pid <= 0:
        return None
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return pid

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    if not handle:
        return None
    try:
        creation = ctypes.c_ulonglong()
        exit_time = ctypes.c_ulonglong()
        kernel_time = ctypes.c_ulonglong()
        user_time = ctypes.c_ulonglong()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        if not ok:
            return None
        return int(creation.value)
    finally:
        kernel32.CloseHandle(handle)


def _process_matches(pid: Any, marker: Any) -> bool:
    if not isinstance(pid, int) or not isinstance(marker, int):
        return False
    return _process_creation_marker(pid) == marker


def _mcp_owned(state: dict[str, Any]) -> bool:
    return _process_matches(
        state.get("mcp_pid"),
        state.get("mcp_creation_marker"),
    )


def _keeper_owned(state: dict[str, Any]) -> bool:
    return _process_matches(
        state.get("keeper_pid"),
        state.get("keeper_creation_marker"),
    )


def _keeper_ready(state: dict[str, Any]) -> bool:
    ready = _read_json(KEEPER_READY_PATH)
    return (
        _keeper_owned(state)
        and ready.get("pid") == state.get("keeper_pid")
        and ready.get("endpoint") == MCP_ENDPOINT_URL
    )


def _resolve_playwright_launch() -> tuple[str, list[str], str]:
    node = shutil.which("node.exe") or shutil.which("node")
    if not node:
        raise RuntimeError("Node.js executable is unavailable")

    local_cli = (
        PROJECT_ROOT
        / ".provider_envs"
        / "browser"
        / "node_modules"
        / "@playwright"
        / "mcp"
        / "cli.js"
    )
    if not local_cli.is_file():
        raise RuntimeError(
            "Browser provider environment is missing; run setup_providers.ps1 "
            "before starting the Browser Runtime"
        )

    package_json = local_cli.parent / "package.json"
    try:
        metadata = json.loads(package_json.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(
            "Browser provider package metadata is missing or invalid; "
            "re-run setup_providers.ps1"
        ) from exc
    if metadata.get("version") != PACKAGE_VERSION:
        raise RuntimeError(
            "Browser provider version drift detected: expected "
            f"{PACKAGE_VERSION}, found {metadata.get('version')!r}"
        )

    return str(Path(node).resolve()), [str(local_cli)], "provider_env"


def _playwright_args() -> list[str]:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return [
        "--host",
        HOST,
        "--port",
        str(MCP_PORT),
        "--shared-browser-context",
        "--browser",
        "msedge",
        "--user-data-dir",
        str(PROFILE_DIR),
        "--image-responses",
        "omit",
        "--output-dir",
        str(OUTPUT_DIR),
    ]


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS


def _terminate_tree(pid: int, marker: int, label: str) -> None:
    if not _process_matches(pid, marker):
        return
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        taskkill = system_root / "System32" / "taskkill.exe"
        completed = subprocess.run(
            [str(taskkill), "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            timeout=15,
            check=False,
        )
        if completed.returncode != 0 and _process_matches(pid, marker):
            raise RuntimeError(f"{label} process could not be stopped cleanly")
    else:
        os.kill(pid, 15)

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not _process_matches(pid, marker):
            return
        time.sleep(0.1)
    raise RuntimeError(f"{label} process is still alive after stop")


def _redact_diagnostic_text(value: str) -> str:
    value = value.replace(str(PROJECT_ROOT), "<PLA_ROOT>")
    value = value.replace(str(Path.home()), "<USER_HOME>")
    value = re.sub(
        r"(?i)(authorization|cookie|password|token|secret)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2<REDACTED>",
        value,
    )
    return value


def _log_tail(path: Path, max_bytes: int = 8192) -> tuple[str, int, bool]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes), os.SEEK_SET)
            raw = handle.read(max_bytes)
    except FileNotFoundError:
        return "", 0, False
    text = raw.decode("utf-8", errors="replace")
    return (
        _redact_diagnostic_text("\n".join(text.splitlines()[-30:])),
        size,
        size > max_bytes,
    )


def browser_runtime_status() -> dict[str, Any]:
    state = _read_state()
    mcp_owned = _mcp_owned(state)
    keeper_owned = _keeper_owned(state)
    keeper_ready = _keeper_ready(state)
    listening = _port_listening(MCP_PORT)

    if mcp_owned and keeper_owned and keeper_ready and listening:
        runtime_state = "ready"
    elif listening and not mcp_owned:
        runtime_state = "conflict"
    elif mcp_owned or keeper_owned or listening:
        runtime_state = "degraded"
    else:
        runtime_state = "stopped"

    return {
        "status": runtime_state,
        "ready": runtime_state == "ready",
        "endpoint": MCP_ENDPOINT_URL,
        "host": HOST,
        "mcp_port": MCP_PORT,
        "mcp_pid": state.get("mcp_pid") if mcp_owned else None,
        "keeper_pid": state.get("keeper_pid") if keeper_owned else None,
        "mcp_owned": mcp_owned,
        "keeper_owned": keeper_owned,
        "keeper_ready": keeper_ready,
        "listening": listening,
        "package": PACKAGE,
        "launch_source": state.get("launch_source") if mcp_owned else None,
        "started_at": (
            state.get("started_at") if (mcp_owned or keeper_owned) else None
        ),
        "profile": ".browser_profiles/v13-default",
        "output_dir": "state/browser",
        "ownership_model": "playwright_mcp_plus_session_keeper",
    }


def browser_runtime_diagnostics() -> dict[str, Any]:
    mcp_tail, mcp_bytes, mcp_truncated = _log_tail(MCP_LOG_PATH)
    keeper_tail, keeper_bytes, keeper_truncated = _log_tail(KEEPER_LOG_PATH)
    return {
        "status": browser_runtime_status(),
        "mcp_log_tail": mcp_tail,
        "mcp_log_bytes": mcp_bytes,
        "mcp_log_tail_truncated": mcp_truncated,
        "keeper_log_tail": keeper_tail,
        "keeper_log_bytes": keeper_bytes,
        "keeper_log_tail_truncated": keeper_truncated,
    }


def _wait_for_port(
    *,
    pid: int,
    marker: int,
    timeout_seconds: float,
    label: str,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _port_listening(MCP_PORT):
            return
        if not _process_matches(pid, marker):
            raise RuntimeError(f"{label} exited before becoming ready")
        time.sleep(0.25)
    raise TimeoutError(
        f"{label} did not listen on {HOST}:{MCP_PORT} within "
        f"{timeout_seconds:.0f}s"
    )


def _wait_for_keeper(pid: int, marker: int) -> None:
    deadline = time.monotonic() + _KEEPER_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        state = _read_state()
        if _keeper_ready(state):
            return
        if not _process_matches(pid, marker):
            raise RuntimeError(
                "Browser Session Keeper exited before becoming ready"
            )
        time.sleep(0.25)
    raise TimeoutError(
        "Browser Session Keeper did not establish its MCP session within "
        f"{_KEEPER_TIMEOUT_SECONDS:.0f}s"
    )


def start_browser_runtime() -> dict[str, Any]:
    with _LOCK:
        current = browser_runtime_status()
        if current["ready"]:
            return {**current, "already_running": True}
        if current["status"] == "conflict":
            raise RuntimeError(
                "Browser Runtime cannot start because port 8931 is occupied "
                "by an unmanaged process"
            )
        if current["status"] == "degraded":
            stop_browser_runtime()

        executable, prefix_args, launch_source = _resolve_playwright_launch()
        if not KEEPER_SCRIPT.is_file():
            raise RuntimeError("Browser Session Keeper script is missing")

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _remove_path(KEEPER_READY_PATH)

        mcp_env = os.environ.copy()
        # Official Playwright MCP supports disabling the HTTP heartbeat.
        # FastMCP Streamable HTTP does not provide the server-push ping channel
        # expected by Playwright MCP, so this prevents false session expiry.
        mcp_env["PLAYWRIGHT_MCP_PING_TIMEOUT_MS"] = "0"

        mcp_log = MCP_LOG_PATH.open("ab")
        try:
            mcp_process = subprocess.Popen(
                [executable, *prefix_args, *_playwright_args()],
                cwd=str(PROJECT_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=mcp_log,
                stderr=subprocess.STDOUT,
                shell=False,
                close_fds=True,
                creationflags=_creation_flags(),
                env=mcp_env,
            )
        finally:
            mcp_log.close()

        mcp_marker = _process_creation_marker(mcp_process.pid)
        if mcp_marker is None:
            raise RuntimeError("Playwright MCP process could not be identified")

        keeper_process = None
        keeper_marker = None
        try:
            _wait_for_port(
                pid=mcp_process.pid,
                marker=mcp_marker,
                timeout_seconds=_START_TIMEOUT_SECONDS,
                label="Playwright MCP runtime",
            )

            keeper_log = KEEPER_LOG_PATH.open("ab")
            try:
                keeper_process = subprocess.Popen(
                    [sys.executable, str(KEEPER_SCRIPT)],
                    cwd=str(PROJECT_ROOT),
                    stdin=subprocess.DEVNULL,
                    stdout=keeper_log,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    close_fds=True,
                    creationflags=_creation_flags(),
                )
            finally:
                keeper_log.close()

            keeper_marker = _process_creation_marker(keeper_process.pid)
            if keeper_marker is None:
                raise RuntimeError(
                    "Browser Session Keeper process could not be identified"
                )

            _write_state(
                {
                    "mcp_pid": mcp_process.pid,
                    "mcp_creation_marker": mcp_marker,
                    "keeper_pid": keeper_process.pid,
                    "keeper_creation_marker": keeper_marker,
                    "launch_source": launch_source,
                    "started_at": _utcnow_iso(),
                    "package": PACKAGE,
                    "endpoint": MCP_ENDPOINT_URL,
                }
            )
            _wait_for_keeper(keeper_process.pid, keeper_marker)
        except Exception:
            if (
                keeper_process is not None
                and isinstance(keeper_marker, int)
            ):
                try:
                    _terminate_tree(
                        keeper_process.pid,
                        keeper_marker,
                        "Browser Session Keeper",
                    )
                except Exception:
                    pass
            try:
                _terminate_tree(
                    mcp_process.pid,
                    mcp_marker,
                    "Playwright MCP",
                )
            except Exception:
                pass
            _remove_path(STATE_PATH)
            _remove_path(KEEPER_READY_PATH)
            raise

        status = browser_runtime_status()
        if not status["ready"]:
            raise RuntimeError("Browser Runtime failed readiness verification")
        return {**status, "already_running": False}


def stop_browser_runtime() -> dict[str, Any]:
    with _LOCK:
        state = _read_state()

        keeper_pid = state.get("keeper_pid")
        keeper_marker = state.get("keeper_creation_marker")
        mcp_pid = state.get("mcp_pid")
        mcp_marker = state.get("mcp_creation_marker")

        had_owned = False
        if isinstance(keeper_pid, int) and isinstance(keeper_marker, int):
            if _process_matches(keeper_pid, keeper_marker):
                had_owned = True
                _terminate_tree(
                    keeper_pid,
                    keeper_marker,
                    "Browser Session Keeper",
                )

        if isinstance(mcp_pid, int) and isinstance(mcp_marker, int):
            if _process_matches(mcp_pid, mcp_marker):
                had_owned = True
                _terminate_tree(
                    mcp_pid,
                    mcp_marker,
                    "Playwright MCP",
                )

        _remove_path(KEEPER_READY_PATH)
        _remove_path(STATE_PATH)

        current = browser_runtime_status()
        if current["status"] == "conflict":
            raise RuntimeError(
                "Browser Runtime stopped its owned processes, but port 8931 "
                "is still occupied by an unmanaged process"
            )
        return {
            **current,
            "stopped": True,
            "already_stopped": not had_owned,
        }
