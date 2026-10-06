"""Lifecycle supervisor for PLA's production out-of-process Execution Runner.

The independent Runner owns default generic one-shot execution after Phase S.
Legacy candidate/shadow/canary entry points remain available for compatibility,
diagnostics, and parity testing; they do not redefine the production default.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
import time
from typing import Any
from uuid import uuid4

from runner_transport import (
    RunnerSpikeClient,
    SPIKE_PROTOCOL_VERSION,
    make_pipe_address,
    read_auth_file,
    start_detached_runner,
    wait_for_runner_state,
    write_auth_file,
)
from runner_process_identity import identity_matches, query_process_identity
from runtime_context import TaskCancelled


PROJECT_ROOT = Path(__file__).resolve().parent
STATE_DIR = PROJECT_ROOT / "state" / "execution_runner"
STATE_PATH = STATE_DIR / "runtime.json"
AUTH_PATH = STATE_DIR / "runner.auth"
_LOCK = RLock()


class RunnerUnavailableBeforeSubmit(RuntimeError):
    """The production Runner was unavailable before any execution was submitted."""

    def __init__(self, message: str, runner: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.runner = dict(runner or {})


class RunnerExecutionIndeterminate(RuntimeError):
    """A request may have been accepted; callers must not blindly execute it again."""

    def __init__(
        self,
        execution_request_id: str,
        message: str,
        runner: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{message}; execution_request_id={execution_request_id}")
        self.execution_request_id = execution_request_id
        self.runner = dict(runner or {})


def _read_state() -> dict[str, Any]:
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _client_from_state(state: dict[str, Any]) -> RunnerSpikeClient:
    address = state.get("address")
    if not isinstance(address, str) or not address:
        raise RuntimeError("execution runner state has no pipe address")
    return RunnerSpikeClient(address, read_auth_file(AUTH_PATH))


_RUNNER_FIELDS = (
    "runner_instance_id",
    "backend",
    "protocol_version",
    "process_id",
    "process_creation_time_100ns",
    "process_image_path",
    "process_isolation",
    "scope",
)


def _runner_from_state(state: dict[str, Any]) -> dict[str, Any]:
    return {key: state.get(key) for key in _RUNNER_FIELDS if state.get(key) is not None}


def _validate_response_identity(
    state: dict[str, Any], runner: dict[str, Any] | None,
) -> dict[str, Any]:
    if state.get("protocol_version") != SPIKE_PROTOCOL_VERSION:
        raise RuntimeError("runner state protocol version is incompatible")
    actual = query_process_identity(state.get("process_id"))
    if not identity_matches(state, actual):
        raise RuntimeError("runner process identity no longer matches durable state")
    if not isinstance(runner, dict):
        raise RuntimeError("runner response omitted identity metadata")
    for key in (
        "runner_instance_id",
        "protocol_version",
        "process_id",
        "process_creation_time_100ns",
    ):
        if runner.get(key) != state.get(key):
            raise RuntimeError(f"runner response identity mismatch: {key}")
    return runner


def _validate_ready_identity(state: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    runner = response.get("runner") if isinstance(response, dict) else None
    if response.get("status") != "ok":
        raise RuntimeError("runner ping returned an invalid response")
    return _validate_response_identity(state, runner)


def execution_runner_status() -> dict[str, Any]:
    with _LOCK:
        state = _read_state()
        if not state:
            return {
                "state": "stopped",
                "running": False,
                "reachable": False,
                "default_backend": True,
                "candidate": True,
                "production_ready": False,
            }

        runner_state = _runner_from_state(state)
        if state.get("status") != "ready":
            return {
                "state": str(state.get("status") or "stopped"),
                "running": False,
                "reachable": False,
                "default_backend": True,
                "candidate": True,
                "production_ready": False,
                "runner": runner_state,
            }

        actual = query_process_identity(state.get("process_id"))
        identity_ok = identity_matches(state, actual)
        protocol_ok = state.get("protocol_version") == SPIKE_PROTOCOL_VERSION
        if not identity_ok or not protocol_ok:
            return {
                "state": "stale_dead_or_reused",
                "running": False,
                "reachable": False,
                "default_backend": True,
                "candidate": True,
                "production_ready": False,
                "recovery_required": False,
                "health": {
                    "process_identity_match": identity_ok,
                    "protocol_compatible": protocol_ok,
                    "ping_identity_match": False,
                },
                "runner": runner_state,
            }

        try:
            response = _client_from_state(state).ping()
            runner = _validate_ready_identity(state, response)
            return {
                "state": "running",
                "running": True,
                "reachable": True,
                "default_backend": True,
                "candidate": True,
                "production_ready": True,
                "recovery_required": False,
                "health": {
                    "process_identity_match": True,
                    "protocol_compatible": True,
                    "ping_identity_match": True,
                    "workload": response.get("workload", {}),
                },
                "runner": runner,
            }
        except Exception as exc:
            return {
                "state": "stale_live",
                "running": False,
                "reachable": False,
                "default_backend": True,
                "candidate": True,
                "production_ready": False,
                "recovery_required": True,
                "health": {
                    "process_identity_match": True,
                    "protocol_compatible": True,
                    "ping_identity_match": False,
                },
                "runner": runner_state,
                "error_type": type(exc).__name__,
            }


def start_execution_runner() -> dict[str, Any]:
    with _LOCK:
        current = execution_runner_status()
        if current.get("running"):
            return {"status": "already_running", **current}
        if current.get("recovery_required"):
            raise RuntimeError(
                "existing Execution Runner process is still alive but unreachable; "
                "refusing to overwrite ownership state"
            )

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _remove(STATE_PATH)
        _remove(AUTH_PATH)
        write_auth_file(AUTH_PATH)
        try:
            os.chmod(AUTH_PATH, 0o600)
        except OSError:
            pass
        address = make_pipe_address()
        start_detached_runner(
            address=address,
            auth_file=AUTH_PATH,
            state_file=STATE_PATH,
        )
        state = wait_for_runner_state(STATE_PATH, timeout=10)
        response = _client_from_state(state).ping()
        runner = _validate_ready_identity(state, response)
        return {
            "status": "started",
            "state": "running",
            "running": True,
            "reachable": True,
            "default_backend": True,
            "candidate": True,
            "production_ready": True,
            "recovery_required": False,
            "health": {
                "process_identity_match": True,
                "protocol_compatible": True,
                "ping_identity_match": True,
            },
            "runner": runner,
        }


def stop_execution_runner() -> dict[str, Any]:
    with _LOCK:
        state = _read_state()
        current_status = execution_runner_status()
        if current_status.get("state") == "stale_dead_or_reused":
            _remove(AUTH_PATH)
            _remove(STATE_PATH)
            return {
                "status": "stale_state_cleaned",
                "state": "stopped",
                "running": False,
                "reachable": False,
                "default_backend": True,
                "candidate": True,
                "production_ready": False,
            }
        if current_status.get("state") == "stale_live":
            return {
                "status": "stop_failed",
                "state": "stale_live",
                "running": False,
                "reachable": False,
                "default_backend": True,
                "candidate": True,
                "production_ready": False,
                "recovery_required": True,
                "runner": current_status.get("runner", {}),
            }
        if state.get("status") != "ready":
            _remove(AUTH_PATH)
            return {
                "status": "already_stopped",
                "state": str(state.get("status") or "stopped"),
                "running": False,
                "default_backend": True,
                "candidate": True,
                "production_ready": False,
            }
        try:
            response = _client_from_state(state).shutdown()
        except Exception as exc:
            return {
                "status": "stop_failed",
                "state": "stale",
                "running": False,
                "default_backend": True,
                "candidate": True,
                "error_type": type(exc).__name__,
                "message": str(exc)[:1000],
            }

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = _read_state()
            if current.get("status") == "stopped":
                _remove(AUTH_PATH)
                return {
                    "status": "stopped",
                    "state": "stopped",
                    "running": False,
                    "reachable": False,
                    "default_backend": True,
                    "candidate": True,
                    "production_ready": False,
                    "runner": response.get("runner", {}),
                }
            time.sleep(0.05)
        return {
            "status": "stop_pending",
            "state": "stopping",
            "running": False,
            "default_backend": True,
            "candidate": True,
        }


def execution_runner_probe() -> dict[str, Any]:
    """Run the production Runner's fixed harmless probe operation."""

    state = _read_state()
    if state.get("status") != "ready":
        raise RuntimeError("execution runner is not running")
    response = _client_from_state(state).probe()
    _validate_response_identity(state, response.get("runner"))
    return response


def _runner_state_matches(expected: dict[str, Any], current: dict[str, Any]) -> bool:
    if current.get("status") != "ready":
        return False
    for key in (
        "runner_instance_id",
        "protocol_version",
        "process_id",
        "process_creation_time_100ns",
        "process_image_path",
    ):
        if current.get(key) != expected.get(key):
            return False
    actual = query_process_identity(current.get("process_id"))
    return identity_matches(current, actual)


def _production_ready_state() -> tuple[dict[str, Any], dict[str, Any]]:
    status = execution_runner_status()
    runner = status.get("runner") if isinstance(status.get("runner"), dict) else {}
    if not status.get("production_ready"):
        raise RunnerUnavailableBeforeSubmit(
            "production execution runner is unavailable before submission",
            runner,
        )
    state = _read_state()
    _validate_response_identity(state, runner)
    return state, dict(runner)


def run_production_oneshot(
    *,
    command: list[str],
    selected_root: str,
    cwd_relative: str,
    timeout: int,
    env_overrides: dict[str, str] | None = None,
    stdin: str | None = None,
    execution_request_id: str | None = None,
) -> dict[str, Any]:
    """Run one production one-shot with request-id recovery and no blind replay.

    Before the first submit, unavailability is reported as safely recoverable by the
    caller. Once a submit may have crossed the pipe, retries reuse the exact same
    request id and are allowed only against the exact same Runner identity. If that
    identity disappears or cannot be queried, the result is indeterminate rather
    than executing the command again elsewhere.
    """

    request_id = execution_request_id or uuid4().hex
    state, runner = _production_ready_state()
    submit_response: dict[str, Any] | None = None
    send_attempted = False
    last_submit_error: Exception | None = None

    for attempt in range(2):
        current = _read_state()
        if not _runner_state_matches(state, current):
            if not send_attempted:
                raise RunnerUnavailableBeforeSubmit(
                    "production execution runner changed before submission",
                    runner,
                )
            raise RunnerExecutionIndeterminate(
                request_id,
                "production Runner identity changed after submission may have started",
                runner,
            )
        try:
            client = _client_from_state(current)
            send_attempted = True
            response = client.submit_resolved(
                execution_request_id=request_id,
                command=command,
                selected_root=selected_root,
                cwd_relative=cwd_relative,
                timeout=timeout,
                env_overrides=env_overrides,
                stdin=stdin,
            )
            _validate_response_identity(state, response.get("runner"))
            submit_response = response
            break
        except Exception as exc:
            last_submit_error = exc
            if attempt == 0:
                time.sleep(0.05)
                continue

    if submit_response is None:
        raise RunnerExecutionIndeterminate(
            request_id,
            "production Runner submit acknowledgement could not be recovered: "
            + type(last_submit_error).__name__,
            runner,
        )

    status = submit_response.get("status")
    if status == "error":
        raise RuntimeError(
            "production execution runner rejected request: "
            + str(
                submit_response.get("message")
                or submit_response.get("error_type")
                or "unknown error"
            )
        )
    if status == "completed":
        return submit_response
    if status not in {"accepted", "running"}:
        raise RuntimeError("production execution runner returned an invalid submit response")

    deadline = time.monotonic() + timeout + 10
    last_poll_error: Exception | None = None
    while time.monotonic() < deadline:
        current = _read_state()
        if not _runner_state_matches(state, current):
            raise RunnerExecutionIndeterminate(
                request_id,
                "production Runner identity changed while execution result was pending",
                runner,
            )
        try:
            response = _client_from_state(current).execution_result(request_id)
            _validate_response_identity(state, response.get("runner"))
        except Exception as exc:
            last_poll_error = exc
            time.sleep(0.05)
            continue

        result_status = response.get("status")
        if result_status == "running":
            time.sleep(0.05)
            continue
        if result_status == "completed":
            return response
        if result_status == "cancelled":
            raise TaskCancelled(
                f"Production execution cancelled; execution_request_id={request_id}"
            )
        if result_status == "error":
            if response.get("error_type") == "KeyError":
                raise RunnerExecutionIndeterminate(
                    request_id,
                    "production Runner lost the accepted execution request",
                    runner,
                )
            raise RuntimeError(
                "production execution runner failed request: "
                + str(response.get("message") or response.get("error_type") or "unknown error")
            )
        raise RuntimeError("production execution runner returned an invalid result response")

    raise RunnerExecutionIndeterminate(
        request_id,
        "production Runner result polling exceeded bounded deadline"
        + (f" after {type(last_poll_error).__name__}" if last_poll_error else ""),
        runner,
    )


def run_candidate_oneshot(
    *,
    command: list[str],
    selected_root: str,
    cwd_relative: str,
    timeout: int,
    env_overrides: dict[str, str] | None = None,
    stdin: str | None = None,
    execution_request_id: str | None = None,
) -> dict[str, Any]:
    """Run one resolved text-mode request through the opt-in candidate Runner."""

    state = _read_state()
    if state.get("status") != "ready":
        raise RuntimeError("candidate execution runner is not running")
    client = _client_from_state(state)
    response = client.run_resolved(
        command=command,
        selected_root=selected_root,
        cwd_relative=cwd_relative,
        timeout=timeout,
        env_overrides=env_overrides,
        stdin=stdin,
        execution_request_id=execution_request_id,
    )
    _validate_response_identity(state, response.get("runner"))
    if response.get("status") == "error":
        raise RuntimeError(
            "candidate execution runner rejected request: "
            + str(response.get("message") or response.get("error_type") or "unknown error")
        )
    if response.get("status") != "completed":
        raise RuntimeError("candidate execution runner returned an invalid response")
    return response


def execution_runner_result(execution_request_id: str) -> dict[str, Any]:
    """Read one retained production Runner result without re-executing the command."""

    state = _read_state()
    if state.get("status") != "ready":
        raise RuntimeError("execution runner is not running")
    response = _client_from_state(state).execution_result(execution_request_id)
    _validate_response_identity(state, response.get("runner"))
    return response


def cancel_execution_runner_request(execution_request_id: str) -> dict[str, Any]:
    """Cancel one Runner-owned execution by exact request id; never accepts a PID."""

    state = _read_state()
    if state.get("status") != "ready":
        raise RuntimeError("execution runner is not running")
    response = _client_from_state(state).cancel_execution(execution_request_id)
    _validate_response_identity(state, response.get("runner"))
    return response


def candidate_execution_result(execution_request_id: str) -> dict[str, Any]:
    """Backward-compatible alias for execution_runner_result."""

    return execution_runner_result(execution_request_id)
