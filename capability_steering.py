"""Deterministic capability steering for generic execution routes.

The steering layer never invokes a replacement capability. It only explains when
one generic route is superseded by a governed specialized capability surface.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from declared_routing import resolve_declared_route


ROUTING_MODES = {
    "specialized_enforced",
    "specialized_preferred",
    "specialized_recommended",
    "generic_allowed",
}


@dataclass(frozen=True)
class SteeringRule:
    id: str
    tool: str
    domain: str
    routing_mode: str
    priority: int
    matcher: Callable[[dict[str, Any]], bool]
    decision_builder: Callable[[dict[str, Any]], dict[str, Any]]

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise ValueError("Steering rule id must be a non-empty string")
        if not self.tool or not isinstance(self.tool, str):
            raise ValueError("Steering rule tool must be a non-empty string")
        if not self.domain or not isinstance(self.domain, str):
            raise ValueError("Steering rule domain must be a non-empty string")
        if self.routing_mode not in ROUTING_MODES:
            raise ValueError(f"Unsupported routing mode: {self.routing_mode}")
        if not isinstance(self.priority, int) or isinstance(self.priority, bool):
            raise TypeError("Steering rule priority must be an integer")
        if not callable(self.matcher) or not callable(self.decision_builder):
            raise TypeError("Steering rule matcher and decision_builder must be callable")

    def public_metadata(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "domain": self.domain,
            "routing_mode": self.routing_mode,
            "priority": self.priority,
        }


class CapabilitySteeringRegistry:
    def __init__(self) -> None:
        self._rules: dict[str, SteeringRule] = {}

    def register(self, rule: SteeringRule) -> None:
        if rule.id in self._rules:
            raise ValueError(f"Duplicate steering rule: {rule.id}")
        self._rules[rule.id] = rule

    def describe(self) -> list[dict[str, Any]]:
        return [
            rule.public_metadata()
            for rule in sorted(
                self._rules.values(),
                key=lambda item: (-item.priority, item.id),
            )
        ]

    def resolve(
        self,
        tool: str,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        candidates = sorted(
            (
                rule
                for rule in self._rules.values()
                if rule.tool == tool and rule.matcher(context)
            ),
            key=lambda item: (-item.priority, item.id),
        )
        if not candidates:
            return None

        rule = candidates[0]
        decision = dict(rule.decision_builder(context))
        decision.setdefault("status", "blocked")
        decision.setdefault("reason", "specialized_capability_required")
        decision.setdefault("domain", rule.domain)
        decision.setdefault("routing_mode", rule.routing_mode)
        decision["steering_rule_id"] = rule.id
        return decision


_GIT_SUGGESTIONS = (
    {
        "name": "git_status",
        "surface": "mcp_tool",
        "purpose": "Read structured repository status.",
    },
    {
        "name": "git_diff",
        "surface": "mcp_tool",
        "purpose": "Read a bounded repository diff.",
    },
    {
        "name": "git_stage",
        "surface": "mcp_tool",
        "purpose": "Stage explicit files with HEAD and SHA-256 preconditions.",
    },
    {
        "name": "git_commit",
        "surface": "mcp_tool",
        "purpose": "Commit only the exact staged files on the expected HEAD.",
    },
    {
        "name": "core.git_tag",
        "surface": "capability",
        "purpose": "Create a governed release tag on the exact clean HEAD.",
    },
    {
        "name": "core.git_push",
        "surface": "capability",
        "purpose": "Push the exact branch and explicit tags through the release gate.",
    },
)

_GIT_PREFERRED = {
    "status": "git_status",
    "diff": "git_diff",
    "add": "git_stage",
    "stage": "git_stage",
    "commit": "git_commit",
    "tag": "core.git_tag",
    "push": "core.git_push",
}

_WINDOWS_SERVICE_SUGGESTIONS = (
    {
        "name": "windows.service_control_preflight",
        "surface": "capability",
        "purpose": (
            "Read service state, local authorization, dependent-service state, "
            "and broker readiness before any write request."
        ),
    },
    {
        "name": "windows.service_control",
        "surface": "capability",
        "purpose": (
            "Perform an allowlisted service start/stop/restart through the "
            "transaction, confirmation, local policy, and UAC boundary."
        ),
    },
    {
        "name": "windows.service_control_status",
        "surface": "capability",
        "purpose": "Verify the durable result of the queued service action.",
    },
)

_SERVICE_OPERATION_BY_COMMAND = {
    "start-service": "start",
    "stop-service": "stop",
    "restart-service": "restart",
}

def _git_subcommand(args: list[str]) -> str | None:
    for item in args:
        token = item.strip().casefold()
        if token in _GIT_PREFERRED:
            return token
    return None


def _prioritized_git_suggestions(subcommand: str | None) -> list[dict[str, str]]:
    preferred = _GIT_PREFERRED.get(subcommand or "")
    ordered = list(_GIT_SUGGESTIONS)
    if preferred is not None:
        ordered.sort(key=lambda item: 0 if item["name"] == preferred else 1)
    return [dict(item) for item in ordered]


def _match_pla_git(context: dict[str, Any]) -> bool:
    program = context.get("program")
    root = context.get("root")
    if not isinstance(program, str) or root != "pla":
        return False
    return Path(program).name.casefold() in {"git", "git.exe"}


def _build_pla_git_decision(context: dict[str, Any]) -> dict[str, Any]:
    program = Path(str(context["program"])).name.casefold()
    args = context.get("args")
    if not isinstance(args, list):
        args = []
    subcommand = _git_subcommand(
        [item for item in args if isinstance(item, str)]
    )
    return {
        "status": "blocked",
        "reason": "specialized_capability_required",
        "attempted_route": {
            "tool": "run_process",
            "program": program,
            "root": "pla",
            "subcommand": subcommand,
        },
        "suggested_capabilities": _prioritized_git_suggestions(subcommand),
        "message": (
            "Git operations against the PLA source repository must use the "
            "governed Git capability surface."
        ),
    }


def _match_windows_service_write(context: dict[str, Any]) -> bool:
    command = context.get("command")
    return (
        isinstance(command, str)
        and command.casefold() in _SERVICE_OPERATION_BY_COMMAND
    )


def _build_windows_service_decision(
    context: dict[str, Any],
) -> dict[str, Any]:
    command = str(context["command"])
    operation = _SERVICE_OPERATION_BY_COMMAND[command.casefold()]
    parameters = context.get("parameters")
    service_name = None
    parameter_keys: list[str] = []
    if isinstance(parameters, dict):
        parameter_keys = sorted(str(key) for key in parameters)
        candidate = parameters.get("Name")
        if isinstance(candidate, str) and candidate:
            service_name = candidate

    return {
        "status": "blocked",
        "reason": "specialized_capability_required",
        "attempted_route": {
            "tool": "run_powershell",
            "command": command,
            "root": context.get("root"),
            "operation": operation,
            "service_name": service_name,
            "parameter_keys": parameter_keys,
        },
        "suggested_capabilities": [
            dict(item) for item in _WINDOWS_SERVICE_SUGGESTIONS
        ],
        "message": (
            "Windows service state changes must use the governed "
            "windows.service_control capability flow."
        ),
    }


STEERING_REGISTRY = CapabilitySteeringRegistry()
STEERING_REGISTRY.register(
    SteeringRule(
        id="pla_source_git",
        tool="run_process",
        domain="git",
        routing_mode="specialized_enforced",
        priority=100,
        matcher=_match_pla_git,
        decision_builder=_build_pla_git_decision,
    )
)
STEERING_REGISTRY.register(
    SteeringRule(
        id="windows_service_write",
        tool="run_powershell",
        domain="windows_service",
        routing_mode="specialized_enforced",
        priority=100,
        matcher=_match_windows_service_write,
        decision_builder=_build_windows_service_decision,
    )
)
def steering_rules() -> list[dict[str, Any]]:
    """Return non-executable public metadata for registered steering rules."""
    return STEERING_REGISTRY.describe()


def routing_catalog() -> list[dict[str, Any]]:
    """Return the maintained routing catalog without executable matcher state."""
    return [
        {
            "id": "pla_source_git",
            "domain": "git",
            "routing_mode": "specialized_enforced",
            "source": {
                "surface": "run_process",
                "context": {
                    "root": "pla",
                    "programs": ["git", "git.exe"],
                },
            },
            "targets": [dict(item) for item in _GIT_SUGGESTIONS],
            "mappings": [],
        },
        {
            "id": "windows_service_write",
            "domain": "windows_service",
            "routing_mode": "specialized_enforced",
            "source": {
                "surface": "run_powershell",
                "context": {
                    "commands": [
                        "Start-Service",
                        "Stop-Service",
                        "Restart-Service",
                    ],
                },
            },
            "targets": [
                dict(item) for item in _WINDOWS_SERVICE_SUGGESTIONS
            ],
            "mappings": [],
        },
    ]


def route_generic_request(
    tool: str,
    arguments: dict[str, Any],
    registry_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate one proposed route without invoking it."""
    allowed_tools = {"run_process", "run_powershell", "capability_invoke"}
    if tool not in allowed_tools:
        raise ValueError(
            "tool must be one of: run_process, run_powershell, capability_invoke"
        )
    if not isinstance(arguments, dict):
        raise TypeError("arguments must be an object")

    if tool == "run_process":
        program = arguments.get("program")
        args = arguments.get("args", [])
        root = arguments.get("root", "workspace")
        if not isinstance(program, str) or not program:
            raise ValueError("run_process route requires a non-empty program")
        if args is None:
            args = []
        if (
            not isinstance(args, list)
            or any(not isinstance(item, str) for item in args)
        ):
            raise TypeError("run_process args must be an array of strings")
        if not isinstance(root, str) or not root:
            raise ValueError("run_process root must be a non-empty string")
        decision = steer_run_process(program, args, root)
        attempted_route = {
            "tool": "run_process",
            "program": Path(program).name.casefold(),
            "root": root,
            "args": list(args),
        }
    elif tool == "run_powershell":
        command = arguments.get("command")
        parameters = arguments.get("parameters")
        root = arguments.get("root", "workspace")
        if not isinstance(command, str) or not command:
            raise ValueError("run_powershell route requires a non-empty command")
        if parameters is not None and not isinstance(parameters, dict):
            raise TypeError("run_powershell parameters must be an object or null")
        if not isinstance(root, str) or not root:
            raise ValueError("run_powershell root must be a non-empty string")
        decision = steer_run_powershell(command, parameters, root)
        attempted_route = {
            "tool": "run_powershell",
            "command": command,
            "root": root,
            "parameter_keys": (
                sorted(str(key) for key in parameters)
                if isinstance(parameters, dict)
                else []
            ),
        }
    else:
        capability_id = arguments.get("capability_id")
        capability_arguments = arguments.get("arguments", {})
        if not isinstance(capability_id, str) or not capability_id:
            raise ValueError(
                "capability_invoke route requires a non-empty capability_id"
            )
        if not isinstance(capability_arguments, dict):
            raise TypeError("capability_invoke arguments must be an object")
        decision = steer_capability_invoke(
            capability_id,
            capability_arguments,
        )
        if decision is None and registry_snapshot is not None:
            decision = resolve_declared_route(
                capability_id,
                capability_arguments,
                registry_snapshot,
            )
        attempted_route = {
            "tool": "capability_invoke",
            "capability_id": capability_id,
            "argument_keys": sorted(
                str(key) for key in capability_arguments
            ),
        }

    if decision is None:
        return {
            "status": "generic_allowed",
            "reason": "no_specialized_route_required",
            "routing_mode": "generic_allowed",
            "attempted_route": attempted_route,
        }

    status_by_mode = {
        "specialized_enforced": "specialized_required",
        "specialized_preferred": "specialized_preferred",
        "specialized_recommended": "specialized_recommended",
        "generic_allowed": "generic_allowed",
    }
    result = {
        "status": status_by_mode[decision["routing_mode"]],
        "reason": decision["reason"],
        "routing_mode": decision["routing_mode"],
        "domain": decision["domain"],
        "attempted_route": decision["attempted_route"],
        "suggested_capabilities": decision["suggested_capabilities"],
        "message": decision["message"],
    }
    if "steering_rule_id" in decision:
        result["steering_rule_id"] = decision["steering_rule_id"]
    if "declared_relation" in decision:
        result["declared_relation"] = decision["declared_relation"]
    return result


def steer_capability_invoke(
    capability_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any] | None:
    """Return advisory steering for one proposed capability invocation."""
    return STEERING_REGISTRY.resolve(
        "capability_invoke",
        {
            "capability_id": capability_id,
            "arguments": arguments,
        },
    )


def steer_run_process(
    program: str,
    args: list[str],
    root: str,
) -> dict[str, Any] | None:
    """Return a steering decision for one generic process route."""
    return STEERING_REGISTRY.resolve(
        "run_process",
        {
            "program": program,
            "args": args,
            "root": root,
        },
    )


def steer_run_powershell(
    command: str,
    parameters: dict[str, Any] | None,
    root: str,
) -> dict[str, Any] | None:
    """Return a steering decision for one generic structured PowerShell route."""
    return STEERING_REGISTRY.resolve(
        "run_powershell",
        {
            "command": command,
            "parameters": parameters,
            "root": root,
        },
    )
