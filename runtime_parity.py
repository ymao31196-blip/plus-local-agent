"""Behavioral parity harness for PLA and an optional local Codex executable.

The harness is intentionally read-mostly. PLA probes use isolated child processes
and persistent sessions but do not mutate repository files. External Codex is
metadata-only by default; task-level Codex probes can be added later once the
actual local executable/runtime entrypoint is known and explicitly selected.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Callable

import local_tools
from session_runtime import InteractiveSessionStore


@dataclass
class ProbeResult:
    name: str
    status: str
    details: dict[str, Any]
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run_probe(name: str, fn: Callable[[], dict[str, Any]]) -> ProbeResult:
    started = time.monotonic()
    try:
        details = fn()
        status = "pass"
    except Exception as exc:
        details = {
            "error_type": type(exc).__name__,
            "error_message": str(exc)[:2000],
        }
        status = "fail"
    return ProbeResult(
        name=name,
        status=status,
        details=details,
        duration_ms=round((time.monotonic() - started) * 1000),
    )


def _wait_session_text(
    store: InteractiveSessionStore,
    session_id: str,
    cursor: int,
    needle: str,
    timeout: float = 5.0,
) -> tuple[int, str]:
    deadline = time.monotonic() + timeout
    seen = ""
    while time.monotonic() < deadline:
        result = store.read(session_id, cursor=cursor, wait_seconds=0.25)
        cursor = result["next_cursor"]
        for event in result["events"]:
            if event["type"] == "output":
                seen += event["data"].get("content", "")
        if needle in seen:
            return cursor, seen
    raise RuntimeError(f"Timed out waiting for {needle!r}; observed {seen!r}")


def _probe_oneshot(root: str) -> dict[str, Any]:
    result = local_tools.run_process(
        "python",
        [
            "-c",
            "import sys; print('PARITY_STDOUT'); sys.stderr.write('PARITY_STDERR\\n')",
        ],
        root=root,
    )
    if result["returncode"] != 0:
        raise RuntimeError(f"unexpected return code: {result['returncode']}")
    if "PARITY_STDOUT" not in result["stdout"]:
        raise RuntimeError("stdout marker missing")
    if "PARITY_STDERR" not in result["stderr"]:
        raise RuntimeError("stderr marker missing")
    return {
        "returncode": result["returncode"],
        "stdout_seen": True,
        "stderr_seen": True,
        "stdout_truncated": result["stdout_truncated"],
        "stderr_truncated": result["stderr_truncated"],
    }


def _probe_timeout(root: str) -> dict[str, Any]:
    result = local_tools.run_process(
        "python",
        ["-c", "import time; print('START', flush=True); time.sleep(2)"],
        timeout=1,
        root=root,
    )
    if result.get("timeout") is not True:
        raise RuntimeError("timeout was not reported")
    return {
        "timeout": True,
        "partial_stdout_seen": "START" in result.get("stdout", ""),
    }


def _probe_session(root: str, terminal_mode: str) -> dict[str, Any]:
    store = InteractiveSessionStore(max_sessions=1)
    opened = store.open(
        "python",
        [
            "-u",
            "-c",
            (
                "import sys,time; "
                "print('PARITY_READY', flush=True); "
                "line=sys.stdin.readline(); "
                "print('PARITY_ECHO:'+line.strip(), flush=True); "
                "time.sleep(30)"
            ),
        ],
        root=root,
        terminal_mode=terminal_mode,
        columns=100,
        rows=25,
    )
    session_id = opened["session_id"]
    cursor = opened["next_cursor"]
    try:
        cursor, ready = _wait_session_text(
            store, session_id, cursor, "PARITY_READY"
        )
        input_text = "parity-input\r" if terminal_mode == "conpty" else "parity-input\n"
        written = store.write(session_id, input_text)
        cursor, echoed = _wait_session_text(
            store, session_id, cursor, "PARITY_ECHO:parity-input"
        )
        resized = None
        if terminal_mode == "conpty":
            resized = store.resize(session_id, 132, 42)
        return {
            "terminal_mode": terminal_mode,
            "ready_seen": "PARITY_READY" in ready,
            "echo_seen": "PARITY_ECHO:parity-input" in echoed,
            "characters_written": written["characters_written"],
            "resized": bool(resized),
            "permissions": opened["permissions"],
            "job_containment": opened["job_containment"],
        }
    finally:
        store.close(session_id)


def run_pla_probe_suite(root: str = "pla") -> list[ProbeResult]:
    return [
        _run_probe("pla.oneshot.stdout_stderr", lambda: _probe_oneshot(root)),
        _run_probe("pla.oneshot.timeout", lambda: _probe_timeout(root)),
        _run_probe("pla.session.pipe", lambda: _probe_session(root, "pipe")),
        _run_probe("pla.session.conpty", lambda: _probe_session(root, "conpty")),
    ]


def _locate_windows_appx_codex() -> str | None:
    """Resolve the current packaged Codex CLI without requiring PATH access."""

    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None

    repository = (
        r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion"
        r"\AppModel\Repository\Packages"
    )
    matches: list[tuple[tuple[int, ...], Path]] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, repository) as root:
            key_count = winreg.QueryInfoKey(root)[0]
            for index in range(key_count):
                try:
                    package_key = winreg.EnumKey(root, index)
                except OSError:
                    continue
                if not package_key.lower().startswith("openai.codex_"):
                    continue
                try:
                    with winreg.OpenKey(root, package_key) as package:
                        package_root = winreg.QueryValueEx(
                            package, "PackageRootFolder"
                        )[0]
                except OSError:
                    continue
                candidate = (
                    Path(package_root) / "app" / "resources" / "codex.exe"
                )
                if not candidate.is_file():
                    continue
                version: tuple[int, ...] = ()
                parts = package_key.split("_")
                if len(parts) > 1:
                    try:
                        version = tuple(int(item) for item in parts[1].split("."))
                    except ValueError:
                        version = ()
                matches.append((version, candidate))
    except OSError:
        return None

    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    return str(matches[0][1].resolve())


def locate_codex(executable: str | None = None) -> str | None:
    candidates = [
        executable,
        os.environ.get("CODEX_EXECUTABLE"),
        shutil.which("codex"),
        shutil.which("codex.exe"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path.resolve())
        resolved = shutil.which(str(candidate))
        if resolved:
            return str(Path(resolved).resolve())
    return _locate_windows_appx_codex()


def probe_codex_metadata(executable: str | None = None) -> ProbeResult:
    def run() -> dict[str, Any]:
        resolved = locate_codex(executable)
        if resolved is None:
            return {
                "available": False,
                "reason": "local_codex_executable_not_found",
            }
        completed = subprocess.run(
            [resolved, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            shell=False,
        )
        return {
            "available": True,
            "executable": resolved,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip()[:2000],
            "stderr": completed.stderr.strip()[:2000],
        }

    result = _run_probe("codex.metadata", run)
    if result.status == "pass" and result.details.get("available") is False:
        result.status = "blocked"
    return result


def build_report(
    *,
    root: str = "pla",
    codex_executable: str | None = None,
) -> dict[str, Any]:
    pla = run_pla_probe_suite(root=root)
    codex = probe_codex_metadata(codex_executable)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": root,
        "pla": [item.to_dict() for item in pla],
        "codex": codex.to_dict(),
        "summary": {
            "pla_passed": sum(item.status == "pass" for item in pla),
            "pla_total": len(pla),
            "codex_status": codex.status,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PLA/Codex runtime parity probes")
    parser.add_argument("--root", default="pla")
    parser.add_argument("--codex-executable", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    report = build_report(
        root=args.root,
        codex_executable=args.codex_executable,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report["summary"]["pla_passed"] == report["summary"]["pla_total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
