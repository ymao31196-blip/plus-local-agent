"""Unified one-shot execution primitives shared by local process surfaces.

This module centralizes child-process launch and carries a truthful description
of the execution boundary. Workspace routing, program allowlists, specialized
capability steering, and user approval decisions are still made above this layer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import subprocess
from typing import Literal, Mapping
from uuid import uuid4

from execution_backend import run_oneshot_backend, runner_metadata_for_preference
from runtime_context import current_trace, observe


ExecutionRoute = Literal[
    "generic_process",
    "structured_powershell",
    "interactive_pipe_session",
    "interactive_conpty_session",
]
ProgramPolicy = Literal["allowlisted_program", "fixed_system_program"]


@dataclass(frozen=True)
class ExecutionPermissionEnvelope:
    """Describe the effective permission boundary for one process launch.

    This is deliberately descriptive before it becomes an enforcement surface.
    "wrapper_policy" means PLA validates the selected root/cwd at the tool
    boundary; it does not claim the child is OS-sandboxed.
    """

    selected_root: str
    cwd: str
    route: ExecutionRoute
    program_policy: ProgramPolicy
    shell: bool = False
    filesystem_boundary: str = "wrapper_policy"
    network_boundary: str = "inherited_runtime"
    elevation_boundary: str = "inherited_runtime"
    sandbox_mode: str = "none"
    confirmation_required: bool = False
    confirmation_supplied: bool = False
    transaction_required: bool = False
    transaction_context: bool = False
    semantic_domain: str = "generic"
    semantic_action: str = "unknown"
    semantic_risk_level: str = "write_local"
    semantic_effect_class: str = "unknown"
    network_intent: str = "possible"
    semantic_confidence: str = "low"
    execution_policy_id: str = "generic_allowed"
    execution_policy_version: str = "1"

    def __post_init__(self) -> None:
        if not isinstance(self.selected_root, str) or not self.selected_root.strip():
            raise ValueError("selected_root must be a non-empty string")
        if not isinstance(self.cwd, str) or not self.cwd:
            raise ValueError("cwd must be a non-empty string")
        if self.route not in {
            "generic_process",
            "structured_powershell",
            "interactive_pipe_session",
            "interactive_conpty_session",
        }:
            raise ValueError(f"Unsupported execution route: {self.route!r}")
        if self.program_policy not in {"allowlisted_program", "fixed_system_program"}:
            raise ValueError(f"Unsupported program policy: {self.program_policy!r}")
        if self.shell is not False:
            raise ValueError("Unified execution requires shell=False")
        if self.filesystem_boundary != "wrapper_policy":
            raise ValueError("Unsupported filesystem boundary")
        if self.network_boundary != "inherited_runtime":
            raise ValueError("Unsupported network boundary")
        if self.elevation_boundary != "inherited_runtime":
            raise ValueError("Unsupported elevation boundary")
        if self.sandbox_mode != "none":
            raise ValueError("Unsupported sandbox mode")
        for name in (
            "confirmation_required",
            "confirmation_supplied",
            "transaction_required",
            "transaction_context",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")
        if self.semantic_risk_level not in {
            "read", "write_local", "write_external", "privileged", "destructive"
        }:
            raise ValueError("Unsupported semantic risk level")
        if self.semantic_effect_class not in {
            "read_only", "local_state_change", "external_state_change",
            "environment_change", "arbitrary_code", "bridge_execution", "unknown",
        }:
            raise ValueError("Unsupported semantic effect class")
        if self.network_intent not in {"none", "read", "write", "possible"}:
            raise ValueError("Unsupported network intent")
        if self.semantic_confidence not in {"high", "medium", "low"}:
            raise ValueError("Unsupported semantic confidence")
        for name in (
            "semantic_domain",
            "semantic_action",
            "execution_policy_id",
            "execution_policy_version",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionRequest:
    """Resolved one-shot process request."""

    command: tuple[str, ...]
    cwd: Path
    timeout: int
    env: Mapping[str, str] | None = None
    env_overrides: Mapping[str, str] | None = None
    stdin: str | bytes | None = None
    text: bool = True
    backend_preference: Literal[
        "default",
        "production_runner",
        "candidate_runner",
        "shadow_candidate",
        "canary_candidate",
    ] = "default"
    execution_request_id: str = field(default_factory=lambda: uuid4().hex)
    permissions: ExecutionPermissionEnvelope | None = None

    def __post_init__(self) -> None:
        if not self.command or any(not isinstance(item, str) or not item for item in self.command):
            raise ValueError("command must contain non-empty string arguments")
        if not isinstance(self.cwd, Path) or not self.cwd.is_absolute():
            raise ValueError("cwd must be an absolute Path")
        if not isinstance(self.timeout, int) or isinstance(self.timeout, bool) or self.timeout < 1:
            raise ValueError("timeout must be a positive integer")
        if self.stdin is not None and not isinstance(self.stdin, (str, bytes)):
            raise TypeError("stdin must be null, string, or bytes")
        if not isinstance(self.text, bool):
            raise TypeError("text must be a boolean")
        if self.backend_preference not in {
            "default",
            "production_runner",
            "candidate_runner",
            "shadow_candidate",
            "canary_candidate",
        }:
            raise ValueError(
                "backend_preference must be 'default', 'production_runner', "
                "'candidate_runner', 'shadow_candidate', or 'canary_candidate'"
            )
        if self.env_overrides is not None and not isinstance(self.env_overrides, Mapping):
            raise TypeError("env_overrides must be null or a mapping")
        if (
            not isinstance(self.execution_request_id, str)
            or len(self.execution_request_id) != 32
            or any(ch not in "0123456789abcdefABCDEF" for ch in self.execution_request_id)
        ):
            raise ValueError("execution_request_id must be a 32-character hexadecimal string")
        if self.permissions is not None and not isinstance(
            self.permissions, ExecutionPermissionEnvelope
        ):
            raise TypeError("permissions must be an ExecutionPermissionEnvelope")


def run_oneshot(request: ExecutionRequest) -> subprocess.CompletedProcess:
    """Execute a validated request using the selected one-shot backend."""

    trace = current_trace()
    runner = runner_metadata_for_preference(request.backend_preference)
    if request.permissions is not None:
        observe(
            "execution_started",
            permissions=request.permissions.to_dict(),
            command_name=Path(request.command[0]).name,
            trace=trace,
            runner=runner,
            backend_preference=request.backend_preference,
            execution_request_id=request.execution_request_id,
        )

    try:
        backend_result = run_oneshot_backend(
            request.command,
            cwd=request.cwd,
            timeout=request.timeout,
            env=request.env,
            env_overrides=request.env_overrides,
            stdin=request.stdin,
            text=request.text,
            backend_preference=request.backend_preference,
            selected_root=(request.permissions.selected_root if request.permissions else None),
            cwd_relative=(request.permissions.cwd if request.permissions else None),
            execution_request_id=request.execution_request_id,
        )
        result = backend_result.completed
        try:
            setattr(result, "_pla_execution_request_id", request.execution_request_id)
        except Exception:
            pass
        runner = dict(backend_result.runner)
        shadow = dict(backend_result.shadow) if backend_result.shadow is not None else None
        canary = dict(backend_result.canary) if backend_result.canary is not None else None
        production = (
            dict(backend_result.production)
            if backend_result.production is not None
            else None
        )
    except Exception as exc:
        try:
            setattr(exc, "_pla_execution_request_id", request.execution_request_id)
        except Exception:
            pass
        attached_runner = getattr(exc, "_pla_runner_metadata", None)
        if isinstance(attached_runner, dict):
            runner = dict(attached_runner)
        attached_production = getattr(exc, "_pla_production_decision", None)
        production = (
            dict(attached_production)
            if isinstance(attached_production, dict)
            else None
        )
        if request.permissions is not None:
            observe(
                "execution_failed",
                permissions=request.permissions.to_dict(),
                error_type=type(exc).__name__,
                trace=trace,
                runner=runner,
                backend_preference=request.backend_preference,
                execution_request_id=request.execution_request_id,
                production=production,
            )
        raise

    if request.permissions is not None:
        if shadow is not None:
            observe(
                "execution_shadow_compared",
                permissions=request.permissions.to_dict(),
                trace=trace,
                backend_preference=request.backend_preference,
                execution_request_id=request.execution_request_id,
                shadow=shadow,
            )
        if canary is not None:
            observe(
                "execution_canary_routed",
                permissions=request.permissions.to_dict(),
                trace=trace,
                backend_preference=request.backend_preference,
                execution_request_id=request.execution_request_id,
                runner=runner,
                canary=canary,
            )
        if production is not None:
            observe(
                "execution_production_routed",
                permissions=request.permissions.to_dict(),
                trace=trace,
                backend_preference=request.backend_preference,
                execution_request_id=request.execution_request_id,
                runner=runner,
                production=production,
            )
        observe(
            "execution_completed",
            permissions=request.permissions.to_dict(),
            returncode=result.returncode,
            trace=trace,
            runner=runner,
            backend_preference=request.backend_preference,
            execution_request_id=request.execution_request_id,
            shadow=shadow,
            canary=canary,
            production=production,
        )
    return result
