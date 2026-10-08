"""Execution-plane backend seam for PLA local process launch.

The production default remains in-process. One-shot callers may opt into the
separate candidate Execution Runner; persistent sessions intentionally remain on
the stable in-process backend until a later phase.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import subprocess
from typing import Literal, Mapping
from uuid import uuid4

from execution.command_semantics import classify_command
from execution.process_controller import controlled_run
from runtime.runtime_context import CURRENT
from execution.shadow_execution_policy import evaluate_shadow_execution
from execution.promotion_execution_policy import evaluate_candidate_promotion


BackendPreference = Literal[
    "default",
    "production_runner",
    "candidate_runner",
    "shadow_candidate",
    "canary_candidate",
]
RUNNER_INSTANCE_ID = uuid4().hex
RUNNER_BACKEND = "in_process_windows" if os.name == "nt" else "in_process_posix"
RUNNER_PROTOCOL_VERSION = "1"


@dataclass(frozen=True)
class BackendExecutionResult:
    completed: subprocess.CompletedProcess
    runner: dict[str, object]
    shadow: dict[str, object] | None = None
    canary: dict[str, object] | None = None
    production: dict[str, object] | None = None


def runner_metadata() -> dict[str, object]:
    """Metadata for the stable default in-process backend."""

    return {
        "runner_instance_id": RUNNER_INSTANCE_ID,
        "backend": RUNNER_BACKEND,
        "protocol_version": RUNNER_PROTOCOL_VERSION,
        "process_id": os.getpid(),
        "process_isolation": "shared_http_process",
    }


def runner_metadata_for_preference(preference: BackendPreference) -> dict[str, object]:
    if preference in {"default", "shadow_candidate"}:
        return runner_metadata()
    if preference == "production_runner":
        return {
            "backend": "production_runner_policy",
            "process_isolation": "policy_selected",
            "production": True,
        }
    if preference == "canary_candidate":
        return {
            "backend": "canary_policy",
            "process_isolation": "policy_selected",
            "candidate": True,
        }
    if preference != "candidate_runner":
        raise ValueError(f"Unsupported backend preference: {preference}")
    from execution.execution_runner_runtime import execution_runner_status

    status = execution_runner_status()
    candidate = status.get("runner")
    if isinstance(candidate, dict):
        return dict(candidate)
    return {
        "backend": "named_pipe_candidate",
        "process_isolation": "separate_process",
        "candidate": True,
        "running": False,
    }


def execution_result_runner_metadata(value) -> dict[str, object]:
    metadata = getattr(value, "_pla_runner_metadata", None)
    return dict(metadata) if isinstance(metadata, dict) else runner_metadata()


def execution_result_shadow_metadata(value) -> dict[str, object] | None:
    metadata = getattr(value, "_pla_shadow_comparison", None)
    return dict(metadata) if isinstance(metadata, dict) else None


def execution_result_canary_metadata(value) -> dict[str, object] | None:
    metadata = getattr(value, "_pla_canary_decision", None)
    return dict(metadata) if isinstance(metadata, dict) else None


def execution_result_production_metadata(value) -> dict[str, object] | None:
    metadata = getattr(value, "_pla_production_decision", None)
    return dict(metadata) if isinstance(metadata, dict) else None


def _attach_runner(value, metadata: dict[str, object]):
    try:
        setattr(value, "_pla_runner_metadata", dict(metadata))
    except Exception:
        pass
    return value


def _attach_shadow(value, metadata: dict[str, object]):
    try:
        setattr(value, "_pla_shadow_comparison", dict(metadata))
    except Exception:
        pass
    return value


def _attach_canary(value, metadata: dict[str, object]):
    try:
        setattr(value, "_pla_canary_decision", dict(metadata))
    except Exception:
        pass
    return value


def _attach_production(value, metadata: dict[str, object]):
    try:
        setattr(value, "_pla_production_decision", dict(metadata))
    except Exception:
        pass
    return value


def _output_fingerprint(value: str | bytes | None) -> dict[str, object]:
    if value is None:
        raw = b""
    elif isinstance(value, bytes):
        raw = value
    else:
        raw = value.encode("utf-8", errors="replace")
    return {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _shadow_comparison(
    primary: subprocess.CompletedProcess,
    shadow: subprocess.CompletedProcess,
    *,
    policy: dict[str, object],
    shadow_runner: dict[str, object],
) -> dict[str, object]:
    primary_stdout = _output_fingerprint(primary.stdout)
    primary_stderr = _output_fingerprint(primary.stderr)
    shadow_stdout = _output_fingerprint(shadow.stdout)
    shadow_stderr = _output_fingerprint(shadow.stderr)
    returncode_match = primary.returncode == shadow.returncode
    stdout_match = primary_stdout == shadow_stdout
    stderr_match = primary_stderr == shadow_stderr
    return {
        "status": "matched" if returncode_match and stdout_match and stderr_match else "mismatched",
        "policy": policy,
        "returncode_match": returncode_match,
        "stdout_match": stdout_match,
        "stderr_match": stderr_match,
        "primary": {
            "returncode": primary.returncode,
            "stdout": primary_stdout,
            "stderr": primary_stderr,
        },
        "shadow": {
            "returncode": shadow.returncode,
            "stdout": shadow_stdout,
            "stderr": shadow_stderr,
        },
        "shadow_runner": shadow_runner,
    }


def _backend_result_from_runner_response(
    response: dict[str, object],
    *,
    command: tuple[str, ...],
    timeout: int,
    production: dict[str, object] | None = None,
) -> BackendExecutionResult:
    metadata = response.get("runner")
    if not isinstance(metadata, dict):
        raise RuntimeError("execution runner omitted runner metadata")
    stdout_record = response.get("stdout") or {}
    stderr_record = response.get("stderr") or {}
    stdout = stdout_record.get("content", "") if isinstance(stdout_record, dict) else ""
    stderr = stderr_record.get("content", "") if isinstance(stderr_record, dict) else ""
    if response.get("timeout") is True:
        exc = subprocess.TimeoutExpired(list(command), timeout, output=stdout, stderr=stderr)
        _attach_runner(exc, metadata)
        if production is not None:
            _attach_production(exc, production)
        raise exc
    returncode = response.get("returncode")
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        raise RuntimeError("execution runner omitted a valid returncode")
    completed = subprocess.CompletedProcess(list(command), returncode, stdout, stderr)
    _attach_runner(completed, metadata)
    if production is not None:
        _attach_production(completed, production)
    return BackendExecutionResult(
        completed=completed,
        runner=dict(metadata),
        production=production,
    )


def run_oneshot_backend(
    command: tuple[str, ...],
    *,
    cwd: Path,
    timeout: int,
    env: Mapping[str, str] | None,
    env_overrides: Mapping[str, str] | None,
    stdin: str | bytes | None,
    text: bool,
    backend_preference: BackendPreference,
    selected_root: str | None,
    cwd_relative: str | None,
    execution_request_id: str | None = None,
) -> BackendExecutionResult:
    """Run one resolved command through the selected execution backend."""

    if backend_preference == "production_runner":
        if text is not True:
            raise ValueError("production_runner currently supports text-mode one-shot execution only")
        if isinstance(stdin, bytes):
            raise ValueError("production_runner does not accept binary stdin")
        if not selected_root or not cwd_relative:
            raise ValueError("production_runner requires selected_root and relative cwd metadata")

        semantic = classify_command(command[0], list(command[1:]))
        repeatability = evaluate_candidate_promotion(
            semantic,
            command=command,
            selected_root=selected_root,
            env_overrides=env_overrides,
            stdin=stdin,
            text=text,
        )
        from execution.execution_runner_runtime import (
            RunnerExecutionIndeterminate,
            RunnerUnavailableBeforeSubmit,
            run_production_oneshot,
        )

        try:
            response = run_production_oneshot(
                command=list(command),
                selected_root=selected_root,
                cwd_relative=cwd_relative,
                timeout=timeout,
                env_overrides=dict(env_overrides or {}),
                stdin=stdin,
                execution_request_id=execution_request_id,
            )
        except RunnerUnavailableBeforeSubmit as exc:
            primary = run_oneshot_backend(
                command,
                cwd=cwd,
                timeout=timeout,
                env=env,
                env_overrides=env_overrides,
                stdin=stdin,
                text=text,
                backend_preference="default",
                selected_root=selected_root,
                cwd_relative=cwd_relative,
                execution_request_id=execution_request_id,
            )
            production = {
                "status": "fallback_pre_submit",
                "execution_request_id": execution_request_id,
                "reason": "runner_unavailable_before_submit",
                "runner": exc.runner,
            }
            _attach_production(primary.completed, production)
            return BackendExecutionResult(
                completed=primary.completed,
                runner=primary.runner,
                production=production,
            )
        except RunnerExecutionIndeterminate as exc:
            production = {
                "status": "indeterminate",
                "execution_request_id": exc.execution_request_id,
                "reason": "runner_result_indeterminate_after_submit",
                "post_submit_fallback_safe": repeatability.fallback_safe,
                "runner": exc.runner,
            }
            if not repeatability.fallback_safe:
                _attach_production(exc, production)
                if exc.runner:
                    _attach_runner(exc, exc.runner)
                raise
            primary = run_oneshot_backend(
                command,
                cwd=cwd,
                timeout=timeout,
                env=env,
                env_overrides=env_overrides,
                stdin=stdin,
                text=text,
                backend_preference="default",
                selected_root=selected_root,
                cwd_relative=cwd_relative,
                execution_request_id=execution_request_id,
            )
            production["status"] = "fallback_indeterminate_read"
            _attach_production(primary.completed, production)
            return BackendExecutionResult(
                completed=primary.completed,
                runner=primary.runner,
                production=production,
            )

        production = {
            "status": "runner",
            "execution_request_id": execution_request_id,
            "post_submit_fallback_safe": repeatability.fallback_safe,
            "runner": response.get("runner", {}),
        }
        return _backend_result_from_runner_response(
            response,
            command=command,
            timeout=timeout,
            production=production,
        )

    if backend_preference == "canary_candidate":
        semantic = classify_command(command[0], list(command[1:]))
        decision = evaluate_candidate_promotion(
            semantic,
            command=command,
            selected_root=selected_root,
            env_overrides=env_overrides,
            stdin=stdin,
            text=text,
        )
        policy = decision.to_dict()
        if not decision.promoted:
            primary = run_oneshot_backend(
                command,
                cwd=cwd,
                timeout=timeout,
                env=env,
                env_overrides=env_overrides,
                stdin=stdin,
                text=text,
                backend_preference="default",
                selected_root=selected_root,
                cwd_relative=cwd_relative,
                execution_request_id=execution_request_id,
            )
            canary = {"status": "skipped", "policy": policy}
            _attach_canary(primary.completed, canary)
            return BackendExecutionResult(
                completed=primary.completed,
                runner=primary.runner,
                canary=canary,
            )

        try:
            candidate = run_oneshot_backend(
                command,
                cwd=cwd,
                timeout=timeout,
                env=env,
                env_overrides=env_overrides,
                stdin=stdin,
                text=text,
                backend_preference="candidate_runner",
                selected_root=selected_root,
                cwd_relative=cwd_relative,
                execution_request_id=execution_request_id,
            )
        except Exception as exc:
            if not decision.fallback_safe:
                raise
            primary = run_oneshot_backend(
                command,
                cwd=cwd,
                timeout=timeout,
                env=env,
                env_overrides=env_overrides,
                stdin=stdin,
                text=text,
                backend_preference="default",
                selected_root=selected_root,
                cwd_relative=cwd_relative,
                execution_request_id=execution_request_id,
            )
            canary = {
                "status": "fallback",
                "policy": policy,
                "error_type": type(exc).__name__,
            }
            _attach_canary(primary.completed, canary)
            return BackendExecutionResult(
                completed=primary.completed,
                runner=primary.runner,
                canary=canary,
            )

        canary = {
            "status": "candidate",
            "policy": policy,
            "candidate_runner": candidate.runner,
        }
        _attach_canary(candidate.completed, canary)
        return BackendExecutionResult(
            completed=candidate.completed,
            runner=candidate.runner,
            canary=canary,
        )

    if backend_preference in {"default", "shadow_candidate"}:
        runner = controlled_run if CURRENT.get() else subprocess.run
        completed = runner(
            list(command),
            cwd=cwd,
            capture_output=True,
            text=text,
            input=stdin,
            timeout=timeout,
            shell=False,
            env=env,
        )
        metadata = runner_metadata()
        _attach_runner(completed, metadata)
        if backend_preference == "default":
            return BackendExecutionResult(completed=completed, runner=metadata)

        semantic = classify_command(command[0], list(command[1:]))
        decision = evaluate_shadow_execution(
            semantic,
            command=command,
            selected_root=selected_root,
            env_overrides=env_overrides,
            stdin=stdin,
            text=text,
        )
        policy = decision.to_dict()
        if not decision.eligible:
            shadow = {
                "status": "skipped",
                "policy": policy,
            }
            _attach_shadow(completed, shadow)
            return BackendExecutionResult(
                completed=completed,
                runner=metadata,
                shadow=shadow,
            )

        try:
            candidate = run_oneshot_backend(
                command,
                cwd=cwd,
                timeout=timeout,
                env=env,
                env_overrides=env_overrides,
                stdin=stdin,
                text=text,
                backend_preference="candidate_runner",
                selected_root=selected_root,
                cwd_relative=cwd_relative,
                execution_request_id=execution_request_id,
            )
        except Exception as exc:
            shadow = {
                "status": (
                    "unavailable"
                    if isinstance(exc, RuntimeError)
                    and "candidate execution runner is not running" in str(exc)
                    else "error"
                ),
                "policy": policy,
                "error_type": type(exc).__name__,
            }
            _attach_shadow(completed, shadow)
            return BackendExecutionResult(
                completed=completed,
                runner=metadata,
                shadow=shadow,
            )

        shadow = _shadow_comparison(
            completed,
            candidate.completed,
            policy=policy,
            shadow_runner=candidate.runner,
        )
        _attach_shadow(completed, shadow)
        return BackendExecutionResult(
            completed=completed,
            runner=metadata,
            shadow=shadow,
        )

    if backend_preference != "candidate_runner":
        raise ValueError(f"Unsupported backend preference: {backend_preference}")
    if text is not True:
        raise ValueError("candidate_runner currently supports text-mode one-shot execution only")
    if isinstance(stdin, bytes):
        raise ValueError("candidate_runner does not accept binary stdin")
    if not selected_root or not cwd_relative:
        raise ValueError("candidate_runner requires selected_root and relative cwd metadata")

    from execution.execution_runner_runtime import run_candidate_oneshot

    response = run_candidate_oneshot(
        command=list(command),
        selected_root=selected_root,
        cwd_relative=cwd_relative,
        timeout=timeout,
        env_overrides=dict(env_overrides or {}),
        stdin=stdin,
        execution_request_id=execution_request_id,
    )
    metadata = response.get("runner")
    if not isinstance(metadata, dict):
        raise RuntimeError("candidate execution runner omitted runner metadata")
    stdout_record = response.get("stdout") or {}
    stderr_record = response.get("stderr") or {}
    stdout = stdout_record.get("content", "") if isinstance(stdout_record, dict) else ""
    stderr = stderr_record.get("content", "") if isinstance(stderr_record, dict) else ""
    if response.get("timeout") is True:
        exc = subprocess.TimeoutExpired(list(command), timeout, output=stdout, stderr=stderr)
        _attach_runner(exc, metadata)
        raise exc
    returncode = response.get("returncode")
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        raise RuntimeError("candidate execution runner omitted a valid returncode")
    completed = subprocess.CompletedProcess(list(command), returncode, stdout, stderr)
    _attach_runner(completed, metadata)
    return BackendExecutionResult(completed=completed, runner=dict(metadata))


def spawn_session_backend(
    command: tuple[str, ...],
    *,
    cwd: Path,
    env: Mapping[str, str],
    terminal_mode: str,
    columns: int,
    rows: int,
):
    """Spawn one persistent session process through the stable in-process backend."""

    if terminal_mode == "pipe":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(
            list(command),
            cwd=cwd,
            env=dict(env),
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
    if terminal_mode == "conpty":
        from execution.conpty_backend import open_conpty

        return open_conpty(
            command,
            cwd=cwd,
            env=env,
            columns=columns,
            rows=rows,
        )
    raise ValueError("terminal_mode must be 'pipe' or 'conpty'")
