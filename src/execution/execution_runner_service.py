"""Standalone process for the out-of-process Execution Runner transport spike."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Lock
from uuid import uuid4

from multiprocessing.connection import Listener

SOURCE_ROOT = Path(__file__).resolve().parents[1]

from execution.runner_transport import (
    MAX_MESSAGE_BYTES,
    SPIKE_PROTOCOL_VERSION,
    _decode,
    _encode,
    read_auth_file,
)
from execution.command_semantics import classify_command
from execution.execution_program_policy import require_allowed_program, validate_env_overrides
from execution.semantic_execution_policy import SPECIALIZED_GIT_ACTIONS, evaluate_semantic_execution
from host.workspace_manager import build_root_policy, load_workspace_roots
from execution.runner_process_identity import current_process_identity
from runtime.runtime_context import terminate_owned_process_tree


PROJECT_ROOT = SOURCE_ROOT.parent
WORKSPACE_ROOT = Path(
    os.environ.get("AGENT_WORKSPACE", str(PROJECT_ROOT / "workspace"))
).resolve()
MAX_OUTPUT_CHARACTERS = 20_000
MAX_ARGS = 128
MAX_ARG_CHARACTERS = 32_768
MAX_STDIN_CHARACTERS = 100_000


def _runner_mutex_name() -> str:
    digest = hashlib.sha256(str(PROJECT_ROOT).casefold().encode("utf-8")).hexdigest()[:24]
    return f"Local\\PLAExecutionRunner-{digest}"


def _acquire_runner_mutex():
    if os.name != "nt":
        return None
    import ctypes

    ERROR_ALREADY_EXISTS = 183
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateMutexW(None, False, _runner_mutex_name())
    if not handle:
        raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        raise RuntimeError("another PLA Execution Runner instance already owns the runtime mutex")
    return handle


def _release_runner_mutex(handle) -> None:
    if handle is None or os.name != "nt":
        return
    import ctypes

    ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)


def _truncate(value: str) -> dict[str, object]:
    if len(value) <= MAX_OUTPUT_CHARACTERS:
        return {"content": value, "truncated": False, "original_length": len(value)}
    return {
        "content": value[-MAX_OUTPUT_CHARACTERS:],
        "truncated": True,
        "original_length": len(value),
    }


def _workspace_config_path() -> Path:
    configured = os.environ.get("AGENT_WORKSPACES_CONFIG")
    if configured:
        return Path(configured).expanduser().resolve()
    return (PROJECT_ROOT / "config" / "workspaces.local.yaml").resolve()


def _resolve_cwd(selected_root: str, cwd_relative: str) -> Path:
    if not isinstance(selected_root, str) or not selected_root:
        raise ValueError("selected_root must be a non-empty string")
    if not isinstance(cwd_relative, str) or not cwd_relative:
        raise ValueError("cwd_relative must be a non-empty string")
    configured_roots = {}
    if selected_root not in {"workspace", "pla"}:
        configured_roots = load_workspace_roots(_workspace_config_path(), PROJECT_ROOT)
    policy = build_root_policy(WORKSPACE_ROOT, PROJECT_ROOT, configured_roots)
    resolved = policy.resolve(selected_root, cwd_relative, "execute")
    if not resolved.target.exists() or not resolved.target.is_dir():
        raise ValueError("resolved cwd must be an existing directory")
    return resolved.target


def _runner_info(runner_instance_id: str) -> dict[str, object]:
    identity = current_process_identity()
    return {
        "runner_instance_id": runner_instance_id,
        "backend": "named_pipe_candidate",
        "protocol_version": SPIKE_PROTOCOL_VERSION,
        "process_id": identity["process_id"],
        "process_creation_time_100ns": identity["creation_time_100ns"],
        "process_image_path": identity["image_path"],
        "process_isolation": "separate_process",
        "scope": "opt_in_text_oneshot",
    }


def _validate_request(request: dict[str, object]) -> None:
    if request.get("protocol_version") != SPIKE_PROTOCOL_VERSION:
        raise ValueError("runner protocol version mismatch")


def _probe(runner: dict[str, object]) -> dict[str, object]:
    """Run one fixed harmless child-process probe; caller supplies no command."""

    script = (
        "import os; "
        "print('PLA_EXECUTION_RUNNER_PROBE_OK', flush=True); "
        "print(os.getpid(), flush=True)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        shell=False,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    child_pid = None
    lines = completed.stdout.splitlines()
    if len(lines) >= 2:
        try:
            child_pid = int(lines[-1])
        except ValueError:
            child_pid = None
    return {
        "status": "completed",
        "returncode": completed.returncode,
        "stdout": _truncate(completed.stdout),
        "stderr": _truncate(completed.stderr),
        "child_pid": child_pid,
        "runner": runner,
    }


def _run_resolved(
    request: dict[str, object],
    runner: dict[str, object],
    record: dict[str, object] | None = None,
) -> dict[str, object]:
    command = request.get("command")
    if (
        not isinstance(command, list)
        or not command
        or len(command) > MAX_ARGS
        or any(
            not isinstance(item, str)
            or not item
            or len(item) > MAX_ARG_CHARACTERS
            for item in command
        )
    ):
        raise ValueError("command must contain 1-128 bounded string arguments")
    require_allowed_program(command[0])

    selected_root = request.get("selected_root")
    cwd_relative = request.get("cwd_relative")
    cwd = _resolve_cwd(selected_root, cwd_relative)

    timeout = request.get("timeout")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 300:
        raise ValueError("timeout must be an integer from 1 to 300")
    stdin = request.get("stdin")
    if stdin is not None and (
        not isinstance(stdin, str) or len(stdin) > MAX_STDIN_CHARACTERS
    ):
        raise ValueError("stdin must be null or a bounded string")
    env_overrides = validate_env_overrides(request.get("env_overrides"))

    semantic = classify_command(command[0], command[1:])
    if evaluate_semantic_execution(semantic) is not None:
        raise ValueError("execution runner rejects commands requiring semantic confirmation")
    if semantic.domain == "git" and semantic.action in SPECIALIZED_GIT_ACTIONS:
        raise ValueError("execution runner rejects Git actions with specialized capabilities")
    if selected_root == "pla" and semantic.domain == "git":
        raise ValueError("execution runner rejects Git commands against the PLA source root")

    lock = record.get("lock") if isinstance(record, dict) else None
    if lock is not None:
        with lock:
            if record.get("cancel_requested"):
                return {
                    "status": "cancelled",
                    "semantic": semantic.to_dict(),
                    "runner": runner,
                }

    child_env = os.environ.copy()
    child_env.update(env_overrides)
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=child_env,
        shell=False,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if lock is not None:
        with lock:
            record["process"] = process
            if record.get("cancel_requested") and process.poll() is None:
                terminate_owned_process_tree(process)

    try:
        try:
            stdout, stderr = process.communicate(input=stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_owned_process_tree(process)
            stdout, stderr = process.communicate()
            return {
                "status": "completed",
                "timeout": True,
                "stdout": _truncate(stdout or ""),
                "stderr": _truncate(stderr or ""),
                "semantic": semantic.to_dict(),
                "runner": runner,
            }
        if isinstance(record, dict) and record.get("cancel_requested"):
            return {
                "status": "cancelled",
                "stdout": _truncate(stdout or ""),
                "stderr": _truncate(stderr or ""),
                "semantic": semantic.to_dict(),
                "runner": runner,
            }
        return {
            "status": "completed",
            "returncode": process.returncode,
            "stdout": _truncate(stdout or ""),
            "stderr": _truncate(stderr or ""),
            "semantic": semantic.to_dict(),
            "runner": runner,
        }
    finally:
        if lock is not None:
            with lock:
                record["process"] = None


MAX_EXECUTION_LEDGER = 256
MAX_ACTIVE_EXECUTIONS = 16


def _execution_request_id(value: object) -> str:
    if not isinstance(value, str) or len(value) != 32:
        raise ValueError("execution_request_id must be a 32-character hexadecimal string")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError("execution_request_id must be hexadecimal") from exc
    return value.lower()


def _execution_fingerprint(request: dict[str, object]) -> str:
    payload = {
        key: request.get(key)
        for key in (
            "command",
            "selected_root",
            "cwd_relative",
            "timeout",
            "env_overrides",
            "stdin",
        )
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _collect_execution(
    request_id: str,
    record: dict[str, object],
    runner: dict[str, object],
) -> dict[str, object]:
    cached = record.get("result")
    if isinstance(cached, dict):
        return cached
    future = record["future"]
    if not future.done():
        return {
            "status": "running",
            "execution_request_id": request_id,
            "runner": runner,
        }
    try:
        result = future.result()
        response = dict(result)
        response["execution_request_id"] = request_id
    except Exception as exc:
        response = {
            "status": "error",
            "execution_request_id": request_id,
            "error_type": type(exc).__name__,
            "message": str(exc)[:2000],
            "runner": runner,
        }
    record["result"] = response
    return response


def _prune_execution_ledger(ledger: dict[str, dict[str, object]]) -> None:
    if len(ledger) <= MAX_EXECUTION_LEDGER:
        return
    for request_id in list(ledger):
        record = ledger[request_id]
        if isinstance(record.get("result"), dict):
            del ledger[request_id]
            if len(ledger) <= MAX_EXECUTION_LEDGER:
                return


def _submit_execution(
    request: dict[str, object],
    runner: dict[str, object],
    executor: ThreadPoolExecutor,
    ledger: dict[str, dict[str, object]],
) -> dict[str, object]:
    request_id = _execution_request_id(request.get("execution_request_id"))
    fingerprint = _execution_fingerprint(request)
    existing = ledger.get(request_id)
    if existing is not None:
        if existing.get("fingerprint") != fingerprint:
            raise ValueError("execution_request_id was already used for a different request")
        return _collect_execution(request_id, existing, runner)

    active = sum(
        1
        for record in ledger.values()
        if not isinstance(record.get("result"), dict) and not record["future"].done()
    )
    if active >= MAX_ACTIVE_EXECUTIONS:
        raise RuntimeError("execution runner active-execution limit reached")

    record: dict[str, object] = {
        "fingerprint": fingerprint,
        "future": None,
        "result": None,
        "process": None,
        "cancel_requested": False,
        "lock": Lock(),
    }
    ledger[request_id] = record
    future = executor.submit(_run_resolved, dict(request), runner, record)
    record["future"] = future
    _prune_execution_ledger(ledger)
    return {
        "status": "accepted",
        "execution_request_id": request_id,
        "runner": runner,
    }


def _execution_result(
    request: dict[str, object],
    runner: dict[str, object],
    ledger: dict[str, dict[str, object]],
) -> dict[str, object]:
    request_id = _execution_request_id(request.get("execution_request_id"))
    record = ledger.get(request_id)
    if record is None:
        raise KeyError("execution_request_id is unknown to this Runner instance")
    return _collect_execution(request_id, record, runner)


def _cancel_execution(
    request: dict[str, object],
    runner: dict[str, object],
    ledger: dict[str, dict[str, object]],
) -> dict[str, object]:
    request_id = _execution_request_id(request.get("execution_request_id"))
    record = ledger.get(request_id)
    if record is None:
        raise KeyError("execution_request_id is unknown to this Runner instance")
    cached = record.get("result")
    if isinstance(cached, dict):
        return {
            "status": "already_terminal",
            "execution_request_id": request_id,
            "result_status": cached.get("status"),
            "runner": runner,
        }
    lock = record.get("lock")
    if lock is None:
        raise RuntimeError("execution record has no cancellation lock")
    with lock:
        record["cancel_requested"] = True
        process = record.get("process")
        if process is not None and process.poll() is None:
            terminate_owned_process_tree(process)
    return {
        "status": "cancellation_requested",
        "execution_request_id": request_id,
        "runner": runner,
    }


def _runner_workload(ledger: dict[str, dict[str, object]]) -> dict[str, int]:
    active = 0
    completed = 0
    for record in ledger.values():
        if isinstance(record.get("result"), dict):
            completed += 1
        elif record["future"].done():
            completed += 1
        else:
            active += 1
    return {
        "active_executions": active,
        "retained_executions": len(ledger),
        "completed_executions": completed,
    }


def _write_state(path: Path, data: dict[str, object]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def serve(address: str, auth_file: Path, state_file: Path) -> int:
    mutex = _acquire_runner_mutex()
    listener = None
    runner = None
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="execution-runner")
    ledger: dict[str, dict[str, object]] = {}
    try:
        authkey = read_auth_file(auth_file)
        runner_instance_id = uuid4().hex
        runner = _runner_info(runner_instance_id)
        listener = Listener(address, family="AF_PIPE", authkey=authkey)
        _write_state(state_file, {"status": "ready", **runner, "address": address})
        running = True
        while running:
            connection = listener.accept()
            try:
                request = _decode(connection.recv_bytes(MAX_MESSAGE_BYTES))
                _validate_request(request)
                op = request.get("op")
                if op == "ping":
                    response = {
                        "status": "ok",
                        "runner": runner,
                        "workload": _runner_workload(ledger),
                    }
                elif op == "probe":
                    response = _probe(runner)
                elif op == "submit_resolved":
                    response = _submit_execution(request, runner, executor, ledger)
                elif op == "execution_result":
                    response = _execution_result(request, runner, ledger)
                elif op == "cancel_execution":
                    response = _cancel_execution(request, runner, ledger)
                elif op == "shutdown":
                    workload = _runner_workload(ledger)
                    if workload["active_executions"]:
                        response = {
                            "status": "error",
                            "error_type": "RunnerBusy",
                            "message": "execution runner has active executions",
                            "workload": workload,
                            "runner": runner,
                        }
                    else:
                        response = {"status": "stopping", "runner": runner}
                        running = False
                else:
                    raise ValueError("unsupported runner operation")
            except Exception as exc:
                response = {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:2000],
                    "runner": runner,
                }
            try:
                connection.send_bytes(_encode(response))
            except (BrokenPipeError, EOFError, OSError):
                # The Control Plane may disappear during an HTTP restart.  The Runner
                # keeps ownership of any submitted execution and retains its result.
                pass
            finally:
                connection.close()
    finally:
        if listener is not None:
            listener.close()
        executor.shutdown(wait=True, cancel_futures=False)
        if runner is not None:
            _write_state(state_file, {"status": "stopped", **runner, "address": address})
        _release_runner_mutex(mutex)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", required=True)
    parser.add_argument("--auth-file", required=True)
    parser.add_argument("--state-file", required=True)
    args = parser.parse_args()
    return serve(args.address, Path(args.auth_file), Path(args.state_file))


if __name__ == "__main__":
    raise SystemExit(main())
