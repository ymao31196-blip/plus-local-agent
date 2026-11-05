"""Privacy-safe, bounded failure telemetry shared by PLA event producers.

No raw messages, arguments, file paths or provider-supplied strings are persisted.
Only locally chosen, fixed vocabulary values and bounded integer exit codes.
"""
from __future__ import annotations

from typing import Any


FAILURE_CLASSES = frozenset({
    "policy_denied", "validation_error", "missing_resource", "decode_error",
    "process_exit", "timeout", "cancelled", "dependency_error",
    "provider_error", "execution_error", "unknown_failure",
})
POLICY_REASONS = frozenset({
    "program_not_allowed", "root_access_denied", "protected_path",
    "outside_root", "confirmation_required", "transaction_required",
    "powershell_parameter_denied", "specialized_capability_required",
    "gate_hook_denied", "component_disabled", "policy_denied_other",
})


def _policy_reason(message: str, exception_name: str = "") -> str | None:
    # Only classify known policy messages; never store the message itself.
    m = message.casefold()
    if m.startswith("program not allowed:"):
        return "program_not_allowed"
    if ("permissionerror: skill is disabled" in m
            or "permissionerror: source is disabled" in m
            or m in {"skill is disabled", "source is disabled"}):
        return "component_disabled"
    if m.startswith("root ") and " does not allow " in m and " access" in m:
        return "root_access_denied"
    if m.startswith(("protected path", "path is protected")):
        return "protected_path"
    if m.startswith((
        "path outside ", "unc paths are not allowed",
        "diff path is outside ", "root path outside",
    )):
        return "outside_root"
    if "requires confirmation=" in m:
        return "confirmation_required"
    if "requires a transaction context" in m:
        return "transaction_required"
    if exception_name == "PowerShellValidationError" and (
        "not allowed" in m or "denied" in m
    ):
        return "powershell_parameter_denied"
    return None


def _exit_code(result: dict[str, Any]) -> int | None:
    value = result.get("returncode")
    # Keep only a signed, machine-sized integer; reject bool and supplied strings.
    if type(value) is int and -(2**31) <= value < 2**31:
        return value
    return None


def _exception_diagnostic_base(exc: BaseException) -> dict[str, Any]:
    name = type(exc).__name__
    try:
        message = str(exc)
    except Exception:
        message = ""
    reason = _policy_reason(message, name)
    if reason is not None:
        return {"failure_class": "policy_denied", "policy_reason": reason}
    if message.casefold().startswith((
        "path does not exist:", "file does not exist:", "not a file:",
    )):
        return {"failure_class": "missing_resource"}
    if message.casefold().startswith((
        "@oai/artifact-tool is not available",
        "configured mcp tools are missing from provider",
    )):
        return {"failure_class": "dependency_error"}
    if isinstance(exc, PermissionError):
        return {"failure_class": "policy_denied", "policy_reason": "policy_denied_other"}
    if isinstance(exc, UnicodeError):
        return {"failure_class": "decode_error"}
    if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
        return {"failure_class": "missing_resource"}
    if name == "PowerShellValidationError" or isinstance(exc, (ValueError, TypeError, KeyError)):
        return {"failure_class": "validation_error"}
    if isinstance(exc, TimeoutError) or name in {"TimeoutExpired", "ProcessTimeout"}:
        return {"failure_class": "timeout"}
    if name in {"TaskCancelled", "CancelledError"}:
        return {"failure_class": "cancelled"}
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return {"failure_class": "dependency_error"}
    if name in {"ToolError", "McpException", "MCPError"}:
        return {"failure_class": "provider_error"}
    return {"failure_class": "execution_error"}

# Fixed vocabulary: never record URLs, hostnames, paths or exception messages.
_NETWORK_STAGES = frozenset({
    "dns", "proxy_connect", "tls_handshake", "response_headers", "body",
})


def exception_diagnostic(exc: BaseException) -> dict[str, Any]:
    output = _exception_diagnostic_base(exc)
    stage = getattr(exc, "network_stage", None)
    if type(stage) is str and stage in _NETWORK_STAGES:
        output["network_stage"] = stage
    return output


def result_diagnostic(result: Any) -> dict[str, Any]:
    """Classify a failed structured tool result without retaining its contents."""
    if not isinstance(result, dict):
        return {"failure_class": "unknown_failure"}

    nested = result.get("data") if isinstance(result.get("data"), dict) else {}
    status = nested.get("status", result.get("status"))
    if status == "blocked":
        reason = nested.get("reason", result.get("reason"))
        policy_reason = (
            "specialized_capability_required"
            if reason == "specialized_capability_required"
            else "policy_denied_other"
        )
        return {"failure_class": "policy_denied", "policy_reason": policy_reason}
    if result.get("timeout") is True or status == "timeout":
        return {"failure_class": "timeout"}
    if status == "cancelled":
        return {"failure_class": "cancelled"}

    error = result.get("error")
    if not isinstance(error, dict):
        error = nested.get("error")
    if isinstance(error, dict):
        error_name = error.get("type")
        known = {
            "UnknownLocalTool": "validation_error",
            "ValidationError": "validation_error",
            "ProcessTimeout": "timeout",
            "TaskCancelled": "cancelled",
            "ProcessExitError": "process_exit",
            "SpecializedCapabilityRequired": "policy_denied",
            "ActionBlocked": "policy_denied",
            "PermissionError": "policy_denied",
            "PowerShellValidationError": "policy_denied",
            "FileNotFoundError": "missing_resource",
            "UnicodeDecodeError": "decode_error",
            "UnicodeError": "decode_error",
        }
        classification = known.get(error_name) if isinstance(error_name, str) else None
        if classification is not None:
            output = {"failure_class": classification}
            if classification == "policy_denied":
                if error_name == "SpecializedCapabilityRequired":
                    output["policy_reason"] = "specialized_capability_required"
                elif error_name == "PowerShellValidationError":
                    output["policy_reason"] = "powershell_parameter_denied"
                else:
                    output["policy_reason"] = "policy_denied_other"
            code = _exit_code(nested) if "returncode" in nested else _exit_code(result)
            if code is not None and classification == "process_exit":
                output["exit_code"] = code
            return output

    code = _exit_code(nested) if "returncode" in nested else _exit_code(result)
    if code is not None and code != 0:
        return {"failure_class": "process_exit", "exit_code": code}
    if status == "not_ready":
        return {"failure_class": "dependency_error"}
    if result.get("is_error") is True:
        return {"failure_class": "provider_error"}
    if status == "precondition_failed":
        return {"failure_class": "validation_error"}
    if isinstance(status, str) and status in {"failed", "error", "partial"}:
        return {"failure_class": "execution_error"}
    return {"failure_class": "unknown_failure"}


def failure_projection(payload: Any) -> dict[str, Any]:
    """Whitelist untrusted legacy event payloads for public activity projections."""
    if not isinstance(payload, dict):
        return {}
    failure_class = payload.get("failure_class")
    if not isinstance(failure_class, str) or failure_class not in FAILURE_CLASSES:
        return {}
    output: dict[str, Any] = {"failure_class": failure_class}
    reason = payload.get("policy_reason")
    if failure_class == "policy_denied" and isinstance(reason, str) and reason in POLICY_REASONS:
        output["policy_reason"] = reason
    code = payload.get("exit_code")
    if failure_class == "process_exit" and type(code) is int and -(2**31) <= code < 2**31:
        output["exit_code"] = code
    stage = payload.get("network_stage")
    if type(stage) is str and stage in _NETWORK_STAGES:
        output["network_stage"] = stage
    return output
