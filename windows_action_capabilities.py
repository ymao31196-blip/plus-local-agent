"""Confirmed, bounded Windows write actions on the stable Capability Broker surface."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from capability_broker import CapabilityBroker
from capability_models import CapabilityDescriptor
from capability_registry import CapabilityRegistry

PLA_ROOT = Path(__file__).resolve().parent
WINDOWS_ACTIONS_CONFIG = PLA_ROOT / "config" / "windows_actions.local.json"
WINDOWS_ACTIONS_CONFIG_VERSION = 1
SERVICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_$.-]{1,256}$")
ELEVATION_STATE_DIR = PLA_ROOT / "state" / "elevation"
ELEVATION_BROKER_STATUS = ELEVATION_STATE_DIR / "broker_status.json"
ELEVATION_BROKER_MAX_AGE_SECONDS = 5.0
ELEVATION_BROKER_START_SCRIPT = PLA_ROOT / "start_elevation_broker.ps1"

_OBJECT_OUTPUT = {"type": "object"}

_ACTION_SCRIPT = """$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
$json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:PLUS_LOCAL_AGENT_WINDOWS_ACTION))
$payload = $json | ConvertFrom-Json

$result = switch ($payload.kind) {
    'flush_dns_cache' {
        Clear-DnsClientCache
        [pscustomobject]@{
            Action = 'flush_dns_cache'
            Status = 'completed'
        }
    }
    default { throw 'Windows action rejected by runtime allowlist' }
}
ConvertTo-Json -InputObject $result -Depth 4 -Compress
"""


def _descriptor(
    capability_id: str,
    remote_name: str,
    title: str,
    description: str,
    input_schema: dict,
    *,
    risk_level: str,
    requires_confirmation: bool,
    requires_transaction: bool,
    tags: tuple[str, ...],
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=capability_id,
        provider_id="windows",
        remote_name=remote_name,
        title=title,
        description=description,
        input_schema=input_schema,
        output_schema=_OBJECT_OUTPUT,
        risk_level=risk_level,
        requires_confirmation=requires_confirmation,
        requires_transaction=requires_transaction,
        tags=tags,
    )


def windows_action_descriptors() -> tuple[CapabilityDescriptor, ...]:
    return (
        _descriptor(
            "windows.action_status",
            "action_status",
            "Windows Action Policy Status",
            "Read the local Windows write-action policy without exposing config paths.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            risk_level="read",
            requires_confirmation=False,
            requires_transaction=False,
            tags=("windows", "action", "policy", "status"),
        ),
        _descriptor(
            "windows.flush_dns_cache",
            "flush_dns_cache",
            "Flush Windows DNS Cache",
            "Clear the local Windows DNS client cache using one fixed reviewed action.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            risk_level="privileged",
            requires_confirmation=True,
            requires_transaction=False,
            tags=("windows", "network", "dns", "write"),
        ),
        _descriptor(
            "windows.service_control_preflight",
            "service_control_preflight",
            "Windows Service Control Preflight",
            (
                "Read service state, local policy authorization, and elevation readiness "
                "before a service-control request is created."
            ),
            {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9_$.-]{1,256}$",
                    },
                    "operation": {
                        "type": "string",
                        "enum": ["start", "stop", "restart"],
                    },
                },
                "required": ["service_name", "operation"],
                "additionalProperties": False,
            },
            risk_level="read",
            requires_confirmation=False,
            requires_transaction=False,
            tags=("windows", "service", "preflight", "read", "elevation"),
        ),
        _descriptor(
            "windows.service_control",
            "service_control",
            "Control Allowlisted Windows Service",
            (
                "Queue one allowlisted Windows service action through the Interactive "
                "Elevation Broker. The service and operation must both be authorized "
                "by deployment-local policy."
            ),
            {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9_$.-]{1,256}$",
                    },
                    "operation": {
                        "type": "string",
                        "enum": ["start", "stop", "restart"],
                    },
                },
                "required": ["service_name", "operation"],
                "additionalProperties": False,
            },
            risk_level="destructive",
            requires_confirmation=True,
            requires_transaction=True,
            tags=("windows", "service", "write", "transaction", "elevation"),
        ),
        _descriptor(
            "windows.service_control_status",
            "service_control_status",
            "Windows Service Control Status",
            "Read one broker-mediated service-control request and its verified result.",
            {
                "type": "object",
                "properties": {
                    "launch_id": {
                        "type": "string",
                        "pattern": "^[0-9a-fA-F]{32}$",
                    }
                },
                "required": ["launch_id"],
                "additionalProperties": False,
            },
            risk_level="read",
            requires_confirmation=False,
            requires_transaction=False,
            tags=("windows", "service", "write", "status", "elevation"),
        ),
        _descriptor(
            "windows.elevation_broker_restart",
            "elevation_broker_restart",
            "Restart Interactive Elevation Broker",
            (
                "Restart only the currently verified PLA Interactive Elevation Broker "
                "and relaunch it through the fixed project startup entrypoint."
            ),
            {"type": "object", "properties": {}, "additionalProperties": False},
            risk_level="privileged",
            requires_confirmation=True,
            requires_transaction=False,
            tags=("windows", "elevation", "runtime", "lifecycle", "restart"),
        ),
    )


def load_windows_action_policy() -> dict[str, Any]:
    if not WINDOWS_ACTIONS_CONFIG.is_file():
        return {
            "version": WINDOWS_ACTIONS_CONFIG_VERSION,
            "services": {},
            "configured": False,
        }
    payload = json.loads(WINDOWS_ACTIONS_CONFIG.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Windows action policy must be an object")
    if set(payload) != {"version", "services"}:
        raise ValueError("Windows action policy contains unsupported fields")
    if payload.get("version") != WINDOWS_ACTIONS_CONFIG_VERSION:
        raise ValueError("Unsupported Windows action policy version")
    services = payload.get("services")
    if not isinstance(services, dict):
        raise ValueError("services must be an object")

    normalized: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    allowed_operations = {"start", "stop", "restart"}
    for service_name, operations in services.items():
        if (
            not isinstance(service_name, str)
            or not SERVICE_NAME_PATTERN.fullmatch(service_name)
        ):
            raise ValueError("Invalid Windows service name in action policy")
        folded = service_name.casefold()
        if folded in seen:
            raise ValueError("Duplicate Windows service name in action policy")
        if not isinstance(operations, list) or not operations:
            raise ValueError("Service operations must be a non-empty array")
        if any(
            not isinstance(operation, str)
            or operation not in allowed_operations
            for operation in operations
        ):
            raise ValueError("Unsupported Windows service operation in action policy")
        if len(set(operations)) != len(operations):
            raise ValueError("Duplicate Windows service operation in action policy")
        seen.add(folded)
        normalized[folded] = {
            "service_name": service_name,
            "operations": tuple(operations),
        }
    return {
        "version": WINDOWS_ACTIONS_CONFIG_VERSION,
        "services": normalized,
        "configured": True,
    }


def windows_action_status() -> dict[str, Any]:
    policy = load_windows_action_policy()
    service_rules = [
        {
            "service_name": rule["service_name"],
            "operations": list(rule["operations"]),
        }
        for rule in policy["services"].values()
    ]
    service_rules.sort(key=lambda item: item["service_name"].casefold())
    broker = _elevation_broker_status()
    return {
        "status": "ready",
        "policy_version": policy["version"],
        "policy_configured": policy["configured"],
        "service_rules": service_rules,
        "service_control_requires_transaction": True,
        "service_control_requires_confirmation": True,
        "dns_flush_requires_confirmation": True,
        "elevation_broker": {
            "status": broker.get("status"),
            "state": broker.get("state"),
            "pid": broker.get("pid"),
            "current_launch_id": broker.get("current_launch_id"),
            "heartbeat_age_seconds": broker.get("heartbeat_age_seconds"),
        },
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def _elevation_broker_status() -> dict[str, Any]:
    if not ELEVATION_BROKER_STATUS.is_file():
        return {
            "status": "not_ready",
            "state": "missing",
            "pid": None,
            "heartbeat_age_seconds": None,
        }
    try:
        value = json.loads(ELEVATION_BROKER_STATUS.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "status": "not_ready",
            "state": "invalid",
            "pid": None,
            "heartbeat_age_seconds": None,
            "message": f"{type(exc).__name__}: {str(exc)[:500]}",
        }

    heartbeat_age = None
    updated_at = value.get("updated_at")
    if isinstance(updated_at, str):
        try:
            updated = datetime.fromisoformat(updated_at)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            heartbeat_age = max(
                0.0,
                (datetime.now(timezone.utc) - updated).total_seconds(),
            )
        except ValueError:
            heartbeat_age = None

    ready = (
        value.get("state") == "running"
        and isinstance(value.get("pid"), int)
        and heartbeat_age is not None
        and heartbeat_age <= ELEVATION_BROKER_MAX_AGE_SECONDS
    )
    return {
        **value,
        "status": "ready" if ready else "not_ready",
        "heartbeat_age_seconds": heartbeat_age,
    }


def _query_service(service_name: str) -> dict[str, Any] | None:
    if not isinstance(service_name, str) or not SERVICE_NAME_PATTERN.fullmatch(service_name):
        raise ValueError("Invalid Windows service name")
    payload = base64.b64encode(
        json.dumps({"service_name": service_name}).encode("utf-8")
    ).decode("ascii")
    script = """$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
$json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:PLUS_LOCAL_AGENT_SERVICE_QUERY))
$payload = $json | ConvertFrom-Json
$service = Get-Service -Name ([string]$payload.service_name) -ErrorAction SilentlyContinue
if ($null -eq $service) {
    [Console]::Out.Write('{}')
    exit 0
}
$result = [pscustomobject]@{
    Name = $service.Name
    DisplayName = $service.DisplayName
    Status = [string]$service.Status
    StartType = [string]$service.StartType
    CanStop = [bool]$service.CanStop
    DependentServices = @($service.DependentServices | ForEach-Object {
        [pscustomobject]@{
            Name = $_.Name
            Status = [string]$_.Status
            StartType = [string]$_.StartType
        }
    })
    RequiredServices = @($service.ServicesDependedOn | ForEach-Object {
        [pscustomobject]@{
            Name = $_.Name
            Status = [string]$_.Status
            StartType = [string]$_.StartType
        }
    })
}
[Console]::Out.Write(($result | ConvertTo-Json -Depth 4 -Compress))
"""
    encoded_script = base64.b64encode(
        script.encode("utf-16-le")
    ).decode("ascii")
    env = os.environ.copy()
    env["PLUS_LOCAL_AGENT_SERVICE_QUERY"] = payload
    completed = subprocess.run(
        [
            str(_powershell_executable()),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Restricted",
            "-EncodedCommand",
            encoded_script,
        ],
        cwd=PLA_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or "Failed to query Windows service")
    raw = completed.stdout.decode("utf-8", errors="replace").strip()
    value = json.loads(raw) if raw else {}
    if value == {}:
        return None
    if not isinstance(value, dict):
        raise RuntimeError("Windows service query returned invalid structured data")
    return value


def _service_execution_blockers(
    service: dict[str, Any] | None,
    operation: str,
) -> list[str]:
    blockers: list[str] = []
    if service is None:
        return ["service_not_found"]

    status = service.get("Status")
    start_type = service.get("StartType")
    can_stop = bool(service.get("CanStop"))
    if status not in {"Running", "Stopped"}:
        blockers.append("service_not_stable")
    if operation in {"start", "restart"} and start_type == "Disabled":
        blockers.append("service_disabled")
    if (
        operation in {"stop", "restart"}
        and status == "Running"
        and not can_stop
    ):
        blockers.append("service_cannot_stop")

    active_dependents = [
        item
        for item in (service.get("DependentServices") or [])
        if isinstance(item, dict) and item.get("Status") != "Stopped"
    ]
    if operation in {"stop", "restart"} and active_dependents:
        blockers.append("active_dependents_running")
    return blockers


def service_control_preflight(service_name: str, operation: str) -> dict[str, Any]:
    if not isinstance(service_name, str) or not SERVICE_NAME_PATTERN.fullmatch(service_name):
        raise ValueError("Invalid Windows service name")
    if operation not in {"start", "stop", "restart"}:
        raise ValueError("Unsupported service operation")

    policy = load_windows_action_policy()
    rule = policy["services"].get(service_name.casefold())
    service = _query_service(service_name)
    broker = _elevation_broker_status()

    policy_authorized = (
        rule is not None and operation in rule["operations"]
    )
    service_blockers = _service_execution_blockers(service, operation)
    blockers = list(service_blockers)
    if not policy_authorized:
        blockers.append("operation_not_authorized")
    if broker.get("status") != "ready":
        blockers.append("elevation_broker_not_ready")
    elif broker.get("current_launch_id"):
        blockers.append("elevation_broker_busy")

    target_status = "Stopped" if operation == "stop" else "Running"
    already_in_target_state = (
        service is not None
        and service.get("Status") == target_status
        and operation in {"start", "stop"}
    )

    return {
        "status": "ready" if not blockers else "blocked",
        "service_name": (
            rule["service_name"] if rule is not None else service_name
        ),
        "operation": operation,
        "service": service,
        "policy_configured": policy["configured"],
        "policy_authorized": policy_authorized,
        "allowed_operations": (
            list(rule["operations"]) if rule is not None else []
        ),
        "elevation_broker": {
            "status": broker.get("status"),
            "state": broker.get("state"),
            "pid": broker.get("pid"),
            "current_launch_id": broker.get("current_launch_id"),
        },
        "already_in_target_state": already_in_target_state,
        "service_execution_blockers": service_blockers,
        "requires_confirmation": True,
        "requires_transaction": True,
        "requires_uac": True,
        "blockers": blockers,
    }


def _process_command_line(pid: int) -> str | None:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError("pid must be a positive integer")
    script = f"""$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
$process = Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}' -ErrorAction SilentlyContinue
if ($null -eq $process) {{
    exit 3
}}
[Console]::Out.Write([string]$process.CommandLine)
"""
    encoded_script = base64.b64encode(
        script.encode("utf-16-le")
    ).decode("ascii")
    completed = subprocess.run(
        [
            str(_powershell_executable()),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Restricted",
            "-EncodedCommand",
            encoded_script,
        ],
        cwd=PLA_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        return None
    value = completed.stdout.decode("utf-8", errors="replace").strip()
    return value or None


def restart_elevation_broker() -> dict[str, Any]:
    broker = _elevation_broker_status()
    if broker.get("status") != "ready":
        raise RuntimeError("Interactive Elevation Broker is not ready")
    if broker.get("current_launch_id"):
        raise RuntimeError("Interactive Elevation Broker is busy")

    old_pid = broker.get("pid")
    if not isinstance(old_pid, int) or old_pid <= 0:
        raise RuntimeError("Interactive Elevation Broker PID is invalid")

    command_line = _process_command_line(old_pid)
    if not command_line:
        raise RuntimeError("Interactive Elevation Broker process could not be verified")
    folded_command_line = command_line.casefold()
    if (
        str(PLA_ROOT).casefold() not in folded_command_line
        or "interactive_elevation_broker.py" not in folded_command_line
    ):
        raise RuntimeError("Interactive Elevation Broker process identity mismatch")

    if not ELEVATION_BROKER_START_SCRIPT.is_file():
        raise RuntimeError("Elevation Broker startup entrypoint is missing")

    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    taskkill = system_root / "System32" / "taskkill.exe"
    if not taskkill.is_file():
        raise RuntimeError("taskkill.exe is unavailable")

    stopped = subprocess.run(
        [str(taskkill), "/PID", str(old_pid), "/T", "/F"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        timeout=10,
        check=False,
    )
    if stopped.returncode != 0:
        stderr = stopped.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or "Failed to stop Interactive Elevation Broker")

    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    process = subprocess.Popen(
        [
            str(_powershell_executable()),
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ELEVATION_BROKER_START_SCRIPT),
        ],
        cwd=PLA_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        creationflags=creationflags,
    )

    deadline = time.monotonic() + 10.0
    last_status: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last_status = _elevation_broker_status()
        new_pid = last_status.get("pid")
        if (
            last_status.get("status") == "ready"
            and isinstance(new_pid, int)
            and new_pid > 0
            and new_pid != old_pid
        ):
            new_command_line = _process_command_line(new_pid)
            if (
                new_command_line
                and str(PLA_ROOT).casefold() in new_command_line.casefold()
                and "interactive_elevation_broker.py" in new_command_line.casefold()
            ):
                return {
                    "status": "completed",
                    "old_pid": old_pid,
                    "new_pid": new_pid,
                    "launcher_pid": process.pid,
                    "broker": last_status,
                }
        time.sleep(0.1)

    subprocess.run(
        [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        timeout=10,
        check=False,
    )
    raise RuntimeError(
        "Timed out waiting for restarted Interactive Elevation Broker"
    )


def _powershell_executable() -> Path:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    executable = (
        system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    if not executable.is_file():
        raise RuntimeError("Windows PowerShell 5.1 is unavailable")
    return executable


def _run_fixed_action(payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    encoded_payload = base64.b64encode(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    encoded_script = base64.b64encode(
        _ACTION_SCRIPT.encode("utf-16-le")
    ).decode("ascii")
    env = os.environ.copy()
    env["PLUS_LOCAL_AGENT_WINDOWS_ACTION"] = encoded_payload
    completed = subprocess.run(
        [
            str(_powershell_executable()),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Restricted",
            "-EncodedCommand",
            encoded_script,
        ],
        cwd=PLA_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        timeout=max(1, min(timeout, 60)),
        check=False,
    )
    stdout = completed.stdout.decode("utf-8", errors="replace").strip()
    stderr = completed.stderr.decode("utf-8", errors="replace").strip()
    if completed.returncode != 0:
        raise RuntimeError(stderr or f"Windows action failed with code {completed.returncode}")
    data = json.loads(stdout) if stdout else {}
    if not isinstance(data, dict):
        raise RuntimeError("Windows action returned an invalid structured result")
    return {
        "status": "completed",
        "returncode": completed.returncode,
        "data": data,
    }


def flush_dns_cache() -> dict[str, Any]:
    return _run_fixed_action({"kind": "flush_dns_cache"})


def service_control(service_name: str, operation: str) -> dict[str, Any]:
    if not isinstance(service_name, str) or not SERVICE_NAME_PATTERN.fullmatch(service_name):
        raise ValueError("Invalid Windows service name")
    if operation not in {"start", "stop", "restart"}:
        raise ValueError("Unsupported service operation")

    policy = load_windows_action_policy()
    rule = policy["services"].get(service_name.casefold())
    if rule is None:
        raise PermissionError("Windows service is not present in the local action allowlist")
    if operation not in rule["operations"]:
        raise PermissionError("Windows service operation is not allowed by local policy")

    service = _query_service(rule["service_name"])
    service_blockers = _service_execution_blockers(service, operation)
    if service_blockers:
        return {
            "status": "precondition_failed",
            "execution_performed": False,
            "elevation_requested": False,
            "service_name": rule["service_name"],
            "operation": operation,
            "service": service,
            "blockers": service_blockers,
        }

    broker = _elevation_broker_status()
    if broker.get("status") != "ready":
        return {
            "status": "not_ready",
            "execution_performed": False,
            "elevation_requested": False,
            "message": "Interactive Elevation Broker is not ready.",
            "broker": broker,
        }
    if broker.get("current_launch_id"):
        return {
            "status": "precondition_failed",
            "execution_performed": False,
            "elevation_requested": False,
            "message": "Interactive Elevation Broker is busy.",
            "broker": broker,
            "blockers": ["elevation_broker_busy"],
        }

    launch_id = uuid4().hex
    created_at = _utc_now()
    ELEVATION_STATE_DIR.mkdir(parents=True, exist_ok=True)
    request_path = ELEVATION_STATE_DIR / f"{launch_id}.request.json"
    status_path = ELEVATION_STATE_DIR / f"{launch_id}.status.json"
    request = {
        "kind": "service_control",
        "launch_id": launch_id,
        "created_at": created_at,
        "service_name": rule["service_name"],
        "operation": operation,
        "timeout_seconds": 60,
    }
    _atomic_json_write(request_path, request)
    _atomic_json_write(
        status_path,
        {
            "launch_id": launch_id,
            "state": "queued",
            "created_at": created_at,
            "updated_at": created_at,
            "returncode": None,
            "win32_error": None,
            "broker_pid": broker.get("pid"),
        },
    )
    return {
        "status": "external_pending",
        "execution_performed": True,
        "elevation_requested": True,
        "launch_id": launch_id,
        "broker_pid": broker.get("pid"),
        "service_name": rule["service_name"],
        "operation": operation,
        "completion": {
            "capability_id": "windows.service_control_status",
            "arguments": {"launch_id": launch_id},
        },
    }


def service_control_status(launch_id: str) -> dict[str, Any]:
    if (
        not isinstance(launch_id, str)
        or len(launch_id) != 32
        or not re.fullmatch(r"[0-9a-fA-F]{32}", launch_id)
    ):
        raise ValueError("Invalid launch_id")

    request_path = ELEVATION_STATE_DIR / f"{launch_id}.request.json"
    status_path = ELEVATION_STATE_DIR / f"{launch_id}.status.json"
    result_path = ELEVATION_STATE_DIR / f"{launch_id}.result.json"
    if not request_path.is_file() or not status_path.is_file():
        return {
            "status": "missing",
            "launch_id": launch_id,
        }

    request = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(request, dict) or request.get("kind") != "service_control":
        return {
            "status": "not_service_control",
            "launch_id": launch_id,
        }

    status = json.loads(status_path.read_text(encoding="utf-8"))
    result = None
    if result_path.is_file():
        value = json.loads(result_path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            result = value

    state = status.get("state")
    returncode = status.get("returncode")
    expected_after = (
        "Stopped" if request.get("operation") == "stop" else "Running"
    )
    result_valid = (
        isinstance(result, dict)
        and result.get("ServiceName") == request.get("service_name")
        and result.get("Operation") == request.get("operation")
        and result.get("AfterStatus") == expected_after
    )

    if state == "completed":
        if returncode != 0:
            semantic_status = "failed"
        elif result is None:
            semantic_status = "result_missing"
        elif not result_valid:
            semantic_status = "result_mismatch"
        else:
            semantic_status = "completed"
    else:
        semantic_status = state or "unknown"

    return {
        "status": semantic_status,
        "launch_id": launch_id,
        "state": state,
        "returncode": returncode,
        "win32_error": status.get("win32_error"),
        "updated_at": status.get("updated_at"),
        "service_name": request.get("service_name"),
        "operation": request.get("operation"),
        "result": result,
    }


def register_windows_action_capabilities(
    registry: CapabilityRegistry,
    broker: CapabilityBroker,
) -> None:
    registry.register_provider(
        "windows",
        windows_action_descriptors(),
        enabled=True,
    )
    broker.register_internal_handler(
        "windows.action_status",
        lambda _args: windows_action_status(),
    )
    broker.register_internal_handler(
        "windows.flush_dns_cache",
        lambda _args: flush_dns_cache(),
    )
    broker.register_internal_handler(
        "windows.service_control_preflight",
        lambda args: service_control_preflight(
            args["service_name"], args["operation"]
        ),
    )
    broker.register_internal_handler(
        "windows.service_control",
        lambda args: service_control(args["service_name"], args["operation"]),
    )
    broker.register_internal_handler(
        "windows.service_control_status",
        lambda args: service_control_status(args["launch_id"]),
    )
    broker.register_internal_handler(
        "windows.elevation_broker_restart",
        lambda _args: restart_elevation_broker(),
    )
