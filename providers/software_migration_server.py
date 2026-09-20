from __future__ import annotations

import hashlib
import json
import ntpath
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any
from uuid import uuid4

import winreg
from fastmcp import FastMCP


mcp = FastMCP("software-migration")

_UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
_REGISTRY_SOURCES = (
    (winreg.HKEY_LOCAL_MACHINE, "HKLM", winreg.KEY_WOW64_64KEY),
    (winreg.HKEY_LOCAL_MACHINE, "HKLM32", winreg.KEY_WOW64_32KEY),
    (winreg.HKEY_CURRENT_USER, "HKCU", winreg.KEY_WOW64_64KEY),
    (winreg.HKEY_CURRENT_USER, "HKCU32", winreg.KEY_WOW64_32KEY),
)
_REGISTRY_FIELDS = (
    "DisplayName",
    "DisplayVersion",
    "Publisher",
    "InstallLocation",
    "UninstallString",
    "QuietUninstallString",
    "EstimatedSize",
    "InstallDate",
    "SystemComponent",
    "WindowsInstaller",
)


def _query_value(key: Any, name: str, default: Any = None) -> Any:
    try:
        value, _ = winreg.QueryValueEx(key, name)
        return value
    except OSError:
        return default


def _iter_uninstall_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for hive, hive_name, view_flag in _REGISTRY_SOURCES:
        try:
            root = winreg.OpenKey(
                hive,
                _UNINSTALL_KEY,
                0,
                winreg.KEY_READ | view_flag,
            )
        except OSError:
            continue

        with root:
            try:
                subkey_count = winreg.QueryInfoKey(root)[0]
            except OSError:
                continue

            for index in range(subkey_count):
                try:
                    subkey_name = winreg.EnumKey(root, index)
                    subkey = winreg.OpenKey(root, subkey_name)
                except OSError:
                    continue

                with subkey:
                    display_name = str(
                        _query_value(subkey, "DisplayName", "")
                    ).strip()
                    if not display_name:
                        continue

                    item: dict[str, Any] = {
                        "display_name": display_name,
                        "registry_source": hive_name,
                        "registry_subkey": subkey_name,
                    }
                    for field in _REGISTRY_FIELDS:
                        item[field] = _query_value(subkey, field)

                    key = (
                        display_name.casefold(),
                        str(item.get("InstallLocation") or "").casefold(),
                        str(item.get("UninstallString") or "").casefold(),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    entries.append(item)

    return entries


def _find_uninstall_matches(query: str) -> list[dict[str, Any]]:
    normalized = query.strip().casefold()
    if not normalized:
        raise ValueError("query must be a non-empty string")

    entries = _iter_uninstall_entries()
    exact = [
        item
        for item in entries
        if str(item.get("display_name", "")).casefold() == normalized
    ]
    if exact:
        return exact

    return [
        item
        for item in entries
        if normalized in str(item.get("display_name", "")).casefold()
    ]


def _absolute_windows_path(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} must be a non-empty path")
    path = PureWindowsPath(cleaned)
    if not path.drive or not path.root:
        raise ValueError(f"{label} must be an absolute Windows path")
    return ntpath.normpath(str(path))


def _drive_of(value: str | None) -> str | None:
    if not value:
        return None
    drive, _ = ntpath.splitdrive(ntpath.normpath(str(value)))
    return drive.upper() or None


def _estimated_size_mb(entry: dict[str, Any]) -> float | None:
    raw = entry.get("EstimatedSize")
    try:
        if raw is None:
            return None
        return round(float(raw) / 1024.0, 1)
    except (TypeError, ValueError):
        return None


def _system_managed_reasons(entry: dict[str, Any], location: str) -> list[str]:
    reasons: list[str] = []
    name = str(entry.get("display_name") or "").casefold()
    publisher = str(entry.get("Publisher") or "").casefold()
    location_norm = ntpath.normcase(ntpath.normpath(location)) if location else ""

    if entry.get("SystemComponent") == 1:
        reasons.append("registry marks this as a Windows system component")

    windows_dir = ntpath.normcase(ntpath.normpath(os.environ.get("WINDIR", r"C:\Windows")))
    if location_norm and (
        location_norm == windows_dir
        or location_norm.startswith(windows_dir + "\\")
    ):
        reasons.append("install location is inside the Windows directory")

    if "\\windowsapps\\" in location_norm:
        reasons.append("install location is managed by WindowsApps/MSIX")

    protected_name_markers = (
        "microsoft edge webview2 runtime",
        "microsoft visual c++",
        "update for x64-based windows systems",
        "windows application compatibility",
    )
    if any(marker in name for marker in protected_name_markers):
        reasons.append("software name matches a shared/system runtime")

    if "driver" in name and any(
        vendor in publisher for vendor in ("nvidia", "intel", "advanced micro devices", "amd")
    ):
        reasons.append("entry appears to be a hardware driver")

    return reasons


def _assess_software_migration(
    query: str,
    target_root: str = r"D:\Apps",
) -> dict[str, Any]:
    target_root_norm = _absolute_windows_path(target_root, "target_root")
    matches = _find_uninstall_matches(query)

    if not matches:
        return {
            "status": "not_found",
            "query": query,
            "target_root": target_root_norm,
            "message": "No matching uninstall entry was found.",
        }

    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "query": query,
            "target_root": target_root_norm,
            "matches": [
                {
                    "display_name": item.get("display_name"),
                    "version": item.get("DisplayVersion"),
                    "publisher": item.get("Publisher"),
                    "install_location": item.get("InstallLocation"),
                    "registry_source": item.get("registry_source"),
                }
                for item in matches[:20]
            ],
            "message": "Multiple uninstall entries match; use a more specific query.",
        }

    entry = matches[0]
    install_location = str(entry.get("InstallLocation") or "").strip()
    current_drive = _drive_of(install_location)
    target_drive = _drive_of(target_root_norm)
    system_reasons = _system_managed_reasons(entry, install_location)
    uninstall_string = str(entry.get("UninstallString") or "").strip()
    quiet_uninstall = str(entry.get("QuietUninstallString") or "").strip()

    if system_reasons:
        classification = "do_not_migrate"
        reason = "This entry appears to be system-managed or shared infrastructure."
    elif current_drive and target_drive and current_drive == target_drive:
        classification = "already_on_target"
        reason = "The recorded install location is already on the target drive."
    elif install_location and uninstall_string:
        classification = "reinstall_preferred"
        reason = (
            "A concrete install location and uninstaller are available; "
            "vendor/WinGet reinstall is safer than moving files in place."
        )
    elif install_location:
        classification = "manual_or_junction_candidate"
        reason = (
            "An install location is known but no uninstall command was found; "
            "manual vendor guidance is required before considering a junction."
        )
    else:
        classification = "needs_manual_inspection"
        reason = (
            "Windows does not report a reliable install location for this entry."
        )

    requires_admin = (
        str(entry.get("registry_source") or "").startswith("HKLM")
        or (install_location and "\\program files" in ntpath.normcase(install_location))
    )

    return {
        "status": "completed",
        "query": query,
        "target_root": target_root_norm,
        "classification": classification,
        "reason": reason,
        "software": {
            "display_name": entry.get("display_name"),
            "version": entry.get("DisplayVersion"),
            "publisher": entry.get("Publisher"),
            "install_location": install_location or None,
            "current_drive": current_drive,
            "estimated_size_mb": _estimated_size_mb(entry),
            "registry_source": entry.get("registry_source"),
            "registry_subkey": entry.get("registry_subkey"),
            "install_date": entry.get("InstallDate"),
            "windows_installer": bool(entry.get("WindowsInstaller")),
        },
        "uninstall": {
            "available": bool(uninstall_string or quiet_uninstall),
            "command": uninstall_string or None,
            "quiet_command": quiet_uninstall or None,
        },
        "system_managed_reasons": system_reasons,
        "requires_admin_likely": bool(requires_admin),
        "recommended_next_actions": [
            "Resolve the corresponding package with the WinGet provider.",
            "Confirm whether the selected installer supports a custom install location.",
            "Prefer uninstall/reinstall to the target path over moving Program Files directly.",
            "Capture rollback information before any write operation.",
            "Use a junction only as an app-specific fallback after verification.",
        ],
        "execution_performed": False,
    }


def _preview_winget_reinstall(
    package_id: str,
    target_directory: str,
    source: str = "winget",
    silent: bool = True,
) -> dict[str, Any]:
    package_id = package_id.strip()
    source = source.strip()
    if not package_id or package_id.startswith("-") or "\n" in package_id or "\r" in package_id:
        raise ValueError("package_id must be a safe non-empty package identifier")
    if not source or source.startswith("-") or "\n" in source or "\r" in source:
        raise ValueError("source must be a safe non-empty source name")

    target = _absolute_windows_path(target_directory, "target_directory")
    argv = [
        "winget",
        "install",
        "--id",
        package_id,
        "--exact",
        "--source",
        source,
        "--location",
        target,
    ]
    if silent:
        argv.append("--silent")

    return {
        "execution_performed": False,
        "package_id": package_id,
        "source": source,
        "target_directory": target,
        "argv": argv,
        "warnings": [
            "WinGet --location is only honored when the selected installer supports a custom location.",
            "This preview does not uninstall the current installation and does not run WinGet.",
            "A real migration must verify the new installation before removing rollback material.",
        ],
        "recommended_sequence": [
            "Resolve and review the exact WinGet package.",
            "Assess the current installation and dependent services/processes.",
            "Create a rollback record.",
            "Uninstall the old copy only after explicit authorization.",
            "Install to the requested location if the installer supports it.",
            "Launch and verify the application.",
            "Clean residual files only after successful verification.",
        ],
    }


def _safe_token(value: str, label: str) -> str:
    cleaned = value.strip()
    if (
        not cleaned
        or cleaned.startswith("-")
        or "\n" in cleaned
        or "\r" in cleaned
    ):
        raise ValueError(f"{label} must be a safe non-empty value")
    return cleaned


def _migration_snapshot(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "display_name": entry.get("display_name"),
        "version": entry.get("DisplayVersion"),
        "publisher": entry.get("Publisher"),
        "install_location": entry.get("InstallLocation"),
        "uninstall_string": entry.get("UninstallString"),
        "quiet_uninstall_string": entry.get("QuietUninstallString"),
        "registry_source": entry.get("registry_source"),
        "registry_subkey": entry.get("registry_subkey"),
        "windows_installer": bool(entry.get("WindowsInstaller")),
    }


def _snapshot_sha256(entry: dict[str, Any]) -> str:
    payload = json.dumps(
        _migration_snapshot(entry),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _prepare_migration(
    query: str,
    package_id: str,
    target_directory: str,
    source: str = "winget",
) -> dict[str, Any]:
    package_id = _safe_token(package_id, "package_id")
    source = _safe_token(source, "source")
    target = _absolute_windows_path(target_directory, "target_directory")
    assessment = _assess_software_migration(query, target)

    if assessment.get("status") != "completed":
        return {
            "status": "not_ready",
            "execution_performed": False,
            "assessment": assessment,
        }

    classification = assessment.get("classification")
    if classification == "already_on_target":
        return {
            "status": "noop",
            "execution_performed": False,
            "assessment": assessment,
            "message": "The application is already on the target drive.",
        }
    if classification != "reinstall_preferred":
        return {
            "status": "not_ready",
            "execution_performed": False,
            "assessment": assessment,
            "message": "This application is not eligible for controlled reinstall migration.",
        }

    matches = _find_uninstall_matches(query)
    if len(matches) != 1:
        return {
            "status": "not_ready",
            "execution_performed": False,
            "message": "The installed application is no longer an unambiguous match.",
        }

    entry = matches[0]
    snapshot = _migration_snapshot(entry)
    snapshot_hash = _snapshot_sha256(entry)
    rollback_target = snapshot.get("install_location")

    return {
        "status": "prepared",
        "execution_performed": False,
        "query": query,
        "package_id": package_id,
        "source": source,
        "target_directory": target,
        "snapshot": snapshot,
        "snapshot_sha256": snapshot_hash,
        "assessment": assessment,
        "orchestration": [
            {
                "step": "preflight",
                "action": "Re-run prepare_migration immediately before destructive work and require the same snapshot_sha256.",
            },
            {
                "step": "uninstall",
                "provider": "software-migration",
                "capability": "execute_winget_uninstall",
                "arguments": {
                    "package_id": package_id,
                    "software_query": query,
                    "source": source,
                    "silent": True,
                },
                "requires_confirmation": True,
            },
            {
                "step": "install",
                "provider": "software-migration",
                "capability": "execute_winget_install",
                "arguments": {
                    "package_id": package_id,
                    "software_query": query,
                    "target_directory": target,
                    "source": source,
                    "silent": True,
                    "require_absent": True,
                },
                "requires_confirmation": True,
            },
            {
                "step": "verify",
                "provider": "software-migration",
                "capability": "verify_migration",
                "arguments": {
                    "query": query,
                    "target_directory": target,
                },
            },
        ],
        "rollback": {
            "strategy": "reinstall_default_or_manual",
            "package_id": package_id,
            "source": source,
            "original_install_location": rollback_target,
            "warning": (
                "WinGet rollback can restore the package but may not recreate a "
                "non-default original install location exactly."
            ),
        },
    }




def _registered_uninstall_argv(entry: dict[str, Any]) -> list[str]:
    raw = str(
        entry.get("QuietUninstallString")
        or entry.get("UninstallString")
        or ""
    ).strip()
    if not raw:
        raise ValueError("No registered uninstall command is available")

    executable: str
    remainder: str
    if raw.startswith('"'):
        closing = raw.find('"', 1)
        if closing <= 1:
            raise ValueError("Registered uninstall command has invalid quoting")
        executable = raw[1:closing]
        remainder = raw[closing + 1 :].strip()
    else:
        lower = raw.lower()
        exe_end = lower.find(".exe")
        if exe_end < 0:
            parts = raw.split(maxsplit=1)
            executable = parts[0]
            remainder = parts[1] if len(parts) > 1 else ""
        else:
            exe_end += 4
            executable = raw[:exe_end].strip()
            remainder = raw[exe_end:].strip()

    executable_path = Path(executable)
    if executable_path.is_absolute():
        resolved = str(executable_path.resolve())
        if not executable_path.exists():
            raise FileNotFoundError(f"Registered uninstaller does not exist: {resolved}")
    else:
        discovered = shutil.which(executable)
        if not discovered:
            raise FileNotFoundError(
                f"Registered uninstaller was not found on PATH: {executable}"
            )
        resolved = discovered

    argv = [resolved]
    if remainder:
        import shlex
        argv.extend(shlex.split(remainder, posix=False))
    return argv




_ELEVATION_STATE_DIR = Path(__file__).resolve().parents[1] / "state" / "elevation"


_BROKER_STATUS_PATH = _ELEVATION_STATE_DIR / "broker_status.json"
_BROKER_HEARTBEAT_MAX_AGE_SECONDS = 5.0


def _elevation_broker_status() -> dict[str, Any]:
    if not _BROKER_STATUS_PATH.is_file():
        return {
            "status": "not_ready",
            "state": "missing",
            "pid": None,
            "heartbeat_age_seconds": None,
        }

    try:
        value = json.loads(_BROKER_STATUS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "status": "not_ready",
            "state": "invalid",
            "pid": None,
            "heartbeat_age_seconds": None,
            "message": f"{type(exc).__name__}: {str(exc)[:500]}",
        }

    updated_at = value.get("updated_at")
    heartbeat_age = None
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
        and heartbeat_age <= _BROKER_HEARTBEAT_MAX_AGE_SECONDS
    )
    return {
        **value,
        "status": "ready" if ready else "not_ready",
        "heartbeat_age_seconds": heartbeat_age,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _launch_elevated_uninstaller(
    executable: str,
    args: list[str],
    software_query: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    broker = _elevation_broker_status()
    if broker.get("status") != "ready":
        return {
            "status": "not_ready",
            "execution_performed": False,
            "elevation_requested": False,
            "message": (
                "Interactive Elevation Broker is not ready. "
                "Start PLA through start_all.ps1 or start_elevation_broker.ps1."
            ),
            "broker": broker,
        }

    launch_id = uuid4().hex
    _ELEVATION_STATE_DIR.mkdir(parents=True, exist_ok=True)
    request_path = _ELEVATION_STATE_DIR / f"{launch_id}.request.json"
    status_path = _ELEVATION_STATE_DIR / f"{launch_id}.status.json"
    request = {
        "kind": "registered_uninstaller",
        "launch_id": launch_id,
        "created_at": _utc_now(),
        "software_query": software_query,
        "executable": executable,
        "args": args,
        "timeout_seconds": timeout_seconds,
    }
    _atomic_json_write(request_path, request)
    _atomic_json_write(
        status_path,
        {
            "launch_id": launch_id,
            "state": "queued",
            "created_at": request["created_at"],
            "updated_at": request["created_at"],
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
        "software_query": software_query,
        "executable": executable,
        "argv": args,
        "completion": {
            "capability_id": "software-migration.elevated_uninstall_status",
            "arguments": {"launch_id": launch_id},
        },
    }


def _launch_elevated_winget_install(
    package_id: str,
    software_query: str,
    target_directory: str,
    source: str = "winget",
    silent: bool = True,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    package_id = _safe_token(package_id, "package_id")
    source = _safe_token(source, "source")
    software_query = software_query.strip()
    if not software_query:
        raise ValueError("software_query must be a non-empty string")
    if not isinstance(timeout_seconds, int) or not 30 <= timeout_seconds <= 900:
        raise ValueError("timeout_seconds must be between 30 and 900")
    if type(silent) is not bool:
        raise TypeError("silent must be a boolean")

    target = _absolute_windows_path(target_directory, "target_directory")
    current_matches = _find_uninstall_matches(software_query)
    if current_matches:
        return {
            "status": "precondition_failed",
            "execution_performed": False,
            "message": (
                "The application is still registered as installed. "
                "Refusing elevated install before uninstall completes."
            ),
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

    winget = shutil.which("winget")
    if not winget:
        raise RuntimeError("winget executable was not found on PATH")

    args = [
        "install",
        "--id",
        package_id,
        "--exact",
        "--source",
        source,
        "--accept-source-agreements",
        "--accept-package-agreements",
        "--disable-interactivity",
        "--location",
        target,
    ]
    if silent:
        args.append("--silent")

    launch_id = uuid4().hex
    _ELEVATION_STATE_DIR.mkdir(parents=True, exist_ok=True)
    request_path = _ELEVATION_STATE_DIR / f"{launch_id}.request.json"
    status_path = _ELEVATION_STATE_DIR / f"{launch_id}.status.json"
    request = {
        "kind": "winget_install",
        "launch_id": launch_id,
        "created_at": _utc_now(),
        "software_query": software_query,
        "package_id": package_id,
        "source": source,
        "target_directory": target,
        "silent": silent,
        "executable": winget,
        "args": args,
        "timeout_seconds": timeout_seconds,
    }
    _atomic_json_write(request_path, request)
    _atomic_json_write(
        status_path,
        {
            "launch_id": launch_id,
            "state": "queued",
            "created_at": request["created_at"],
            "updated_at": request["created_at"],
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
        "package_id": package_id,
        "software_query": software_query,
        "target_directory": target,
        "executable": winget,
        "argv": args,
        "completion": {
            "capability_id": "software-migration.elevated_install_status",
            "arguments": {"launch_id": launch_id},
        },
    }


def _elevated_install_status(launch_id: str) -> dict[str, Any]:
    launch_id = launch_id.strip()
    if len(launch_id) != 32 or not launch_id.isalnum():
        raise ValueError("launch_id is invalid")

    request_path = _ELEVATION_STATE_DIR / f"{launch_id}.request.json"
    status_path = _ELEVATION_STATE_DIR / f"{launch_id}.status.json"
    if not request_path.is_file():
        raise ValueError(f"Unknown elevation launch_id: {launch_id}")

    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("kind") != "winget_install":
        raise ValueError("launch_id does not refer to an elevated WinGet install")

    status = (
        json.loads(status_path.read_text(encoding="utf-8"))
        if status_path.is_file()
        else {
            "launch_id": launch_id,
            "state": "queued",
            "returncode": None,
            "win32_error": None,
        }
    )
    worker_state = str(status.get("state") or "queued")
    verification = None

    if worker_state in {"queued", "requesting_elevation", "running"}:
        semantic_status = "external_pending"
    elif worker_state == "completed":
        if int(status.get("returncode") or 0) == 0:
            verification = _verify_migration(
                str(request["software_query"]),
                str(request["target_directory"]),
            )
            semantic_status = (
                "completed"
                if verification.get("verified") is True
                else "failed"
            )
        else:
            semantic_status = "failed"
    elif worker_state == "timeout":
        semantic_status = "timeout"
    else:
        semantic_status = "failed"

    return {
        "status": semantic_status,
        "launch_id": launch_id,
        "worker_state": worker_state,
        "returncode": status.get("returncode"),
        "win32_error": status.get("win32_error"),
        "package_id": request.get("package_id"),
        "software_query": request.get("software_query"),
        "target_directory": request.get("target_directory"),
        "verification": verification,
        "created_at": status.get("created_at"),
        "updated_at": status.get("updated_at"),
    }


def _elevated_uninstall_status(launch_id: str) -> dict[str, Any]:
    launch_id = launch_id.strip()
    if len(launch_id) != 32 or not launch_id.isalnum():
        raise ValueError("launch_id is invalid")

    request_path = _ELEVATION_STATE_DIR / f"{launch_id}.request.json"
    status_path = _ELEVATION_STATE_DIR / f"{launch_id}.status.json"
    if not request_path.is_file():
        raise ValueError(f"Unknown elevation launch_id: {launch_id}")

    request = json.loads(request_path.read_text(encoding="utf-8"))
    status = (
        json.loads(status_path.read_text(encoding="utf-8"))
        if status_path.is_file()
        else {
            "launch_id": launch_id,
            "state": "queued",
            "returncode": None,
            "win32_error": None,
        }
    )
    software_query = str(request["software_query"])
    remaining = _find_uninstall_matches(software_query)
    worker_state = str(status.get("state") or "queued")

    if worker_state in {"queued", "requesting_elevation", "launched", "running"}:
        semantic_status = "external_pending"
    elif worker_state == "completed":
        semantic_status = (
            "completed"
            if int(status.get("returncode") or 0) == 0 and not remaining
            else "failed"
        )
    elif worker_state == "timeout":
        semantic_status = "timeout"
    else:
        semantic_status = "failed"

    return {
        "status": semantic_status,
        "launch_id": launch_id,
        "worker_state": worker_state,
        "broker_pid": status.get("broker_pid"),
        "returncode": status.get("returncode"),
        "win32_error": status.get("win32_error"),
        "software_query": software_query,
        "remaining_matches": [
            {
                "display_name": item.get("display_name"),
                "version": item.get("DisplayVersion"),
                "install_location": item.get("InstallLocation"),
            }
            for item in remaining[:20]
        ],
        "created_at": status.get("created_at"),
        "updated_at": status.get("updated_at"),
    }


def _execute_registered_uninstaller(
    software_query: str,
    extra_args: list[str] | None = None,
    timeout_seconds: int = 300,
    elevate: bool = False,
) -> dict[str, Any]:
    software_query = software_query.strip()
    if not software_query:
        raise ValueError("software_query must be a non-empty string")
    if not isinstance(timeout_seconds, int) or not 30 <= timeout_seconds <= 900:
        raise ValueError("timeout_seconds must be between 30 and 900")

    matches = _find_uninstall_matches(software_query)
    if not matches:
        return {
            "status": "noop",
            "execution_performed": False,
            "message": "The application is not registered as installed.",
        }
    if len(matches) != 1:
        return {
            "status": "precondition_failed",
            "execution_performed": False,
            "message": "The installed application is not an unambiguous match.",
            "match_count": len(matches),
        }

    argv = _registered_uninstall_argv(matches[0])
    for index, value in enumerate(extra_args or []):
        if not isinstance(value, str):
            raise TypeError(f"extra_args[{index}] must be a string")
        if not value or "\x00" in value or len(value) > 512:
            raise ValueError(f"extra_args[{index}] is invalid")
        argv.append(value)

    if elevate:
        return _launch_elevated_uninstaller(
            executable=argv[0],
            args=argv[1:],
            software_query=software_query,
            timeout_seconds=timeout_seconds,
        )
    else:
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            returncode = completed.returncode
            stdout = completed.stdout.decode("utf-8", "replace")
            stderr = completed.stderr.decode("utf-8", "replace")
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
            if not isinstance(stdout, str):
                stdout = stdout.decode("utf-8", "replace")
            if not isinstance(stderr, str):
                stderr = stderr.decode("utf-8", "replace")
            return {
                "status": "timeout",
                "execution_performed": True,
                "software_query": software_query,
                "executable": argv[0],
                "argv": argv[1:],
                "elevation_requested": False,
                "stdout": stdout[-12000:],
                "stderr": stderr[-12000:],
                "warning": "The registered uninstaller may have made partial changes before timeout.",
            }

    remaining = _find_uninstall_matches(software_query)
    if returncode == 0 and remaining:
        for _ in range(20):
            time.sleep(0.5)
            remaining = _find_uninstall_matches(software_query)
            if not remaining:
                break

    return {
        "status": "completed"
        if returncode == 0 and not remaining
        else "failed",
        "execution_performed": True,
        "returncode": returncode,
        "software_query": software_query,
        "executable": argv[0],
        "argv": argv[1:],
        "elevation_requested": elevate,
        "stdout": stdout[-12000:],
        "stderr": stderr[-12000:],
        "remaining_matches": [
            {
                "display_name": item.get("display_name"),
                "version": item.get("DisplayVersion"),
                "install_location": item.get("InstallLocation"),
            }
            for item in remaining[:20]
        ],
    }


def _execute_winget_uninstall(
    package_id: str,
    software_query: str,
    source: str = "winget",
    silent: bool = True,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    package_id = _safe_token(package_id, "package_id")
    source = _safe_token(source, "source")
    software_query = software_query.strip()
    if not software_query:
        raise ValueError("software_query must be a non-empty string")
    if not isinstance(timeout_seconds, int) or not 30 <= timeout_seconds <= 900:
        raise ValueError("timeout_seconds must be between 30 and 900")

    matches = _find_uninstall_matches(software_query)
    if not matches:
        return {
            "status": "noop",
            "execution_performed": False,
            "message": "The application is not registered as installed.",
        }
    if len(matches) != 1:
        return {
            "status": "precondition_failed",
            "execution_performed": False,
            "message": "The installed application is not an unambiguous match.",
            "match_count": len(matches),
        }

    winget = shutil.which("winget")
    if not winget:
        raise RuntimeError("winget executable was not found on PATH")

    argv = [
        winget,
        "uninstall",
        "--id",
        package_id,
        "--exact",
        "--source",
        source,
        "--accept-source-agreements",
        "--disable-interactivity",
    ]
    if silent:
        argv.append("--silent")

    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        stdout = completed.stdout.decode("utf-8", "replace")
        stderr = completed.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        if not isinstance(stdout, str):
            stdout = stdout.decode("utf-8", "replace")
        if not isinstance(stderr, str):
            stderr = stderr.decode("utf-8", "replace")
        return {
            "status": "timeout",
            "execution_performed": True,
            "package_id": package_id,
            "software_query": software_query,
            "argv": argv[1:],
            "stdout": stdout[-12000:],
            "stderr": stderr[-12000:],
            "warning": "The uninstaller may have made partial changes before timeout.",
        }

    remaining = _find_uninstall_matches(software_query)
    if completed.returncode == 0 and remaining:
        for _ in range(10):
            time.sleep(0.5)
            remaining = _find_uninstall_matches(software_query)
            if not remaining:
                break

    completed_ok = completed.returncode == 0 and not remaining
    return {
        "status": "completed" if completed_ok else "failed",
        "execution_performed": True,
        "returncode": completed.returncode,
        "package_id": package_id,
        "software_query": software_query,
        "argv": argv[1:],
        "stdout": stdout[-12000:],
        "stderr": stderr[-12000:],
        "remaining_matches": [
            {
                "display_name": item.get("display_name"),
                "version": item.get("DisplayVersion"),
                "install_location": item.get("InstallLocation"),
            }
            for item in remaining[:20]
        ],
    }

def _execute_winget_install(
    package_id: str,
    software_query: str,
    target_directory: str | None = None,
    source: str = "winget",
    silent: bool = True,
    require_absent: bool = True,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    package_id = _safe_token(package_id, "package_id")
    source = _safe_token(source, "source")
    software_query = software_query.strip()
    if not software_query:
        raise ValueError("software_query must be a non-empty string")
    if not isinstance(timeout_seconds, int) or not 30 <= timeout_seconds <= 900:
        raise ValueError("timeout_seconds must be between 30 and 900")

    target = (
        _absolute_windows_path(target_directory, "target_directory")
        if target_directory
        else None
    )

    current_matches = _find_uninstall_matches(software_query)
    if require_absent and current_matches:
        return {
            "status": "precondition_failed",
            "execution_performed": False,
            "message": (
                "The application is still registered as installed. "
                "Refusing to install the migration target before uninstall completes."
            ),
            "matches": [
                {
                    "display_name": item.get("display_name"),
                    "version": item.get("DisplayVersion"),
                    "install_location": item.get("InstallLocation"),
                }
                for item in current_matches[:20]
            ],
        }

    winget = shutil.which("winget")
    if not winget:
        raise RuntimeError("winget executable was not found on PATH")

    argv = [
        winget,
        "install",
        "--id",
        package_id,
        "--exact",
        "--source",
        source,
        "--accept-source-agreements",
        "--accept-package-agreements",
        "--disable-interactivity",
    ]
    if target is not None:
        argv.extend(["--location", target])
    if silent:
        argv.append("--silent")

    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        stdout = completed.stdout.decode("utf-8", "replace")
        stderr = completed.stderr.decode("utf-8", "replace")
        return {
            "status": "completed" if completed.returncode == 0 else "failed",
            "execution_performed": True,
            "returncode": completed.returncode,
            "package_id": package_id,
            "software_query": software_query,
            "target_directory": target,
            "argv": argv[1:],
            "stdout": stdout[-12000:],
            "stderr": stderr[-12000:],
        }
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or b"")
        stderr = (exc.stderr or b"")
        if isinstance(stdout, str):
            stdout_text = stdout
        else:
            stdout_text = stdout.decode("utf-8", "replace")
        if isinstance(stderr, str):
            stderr_text = stderr
        else:
            stderr_text = stderr.decode("utf-8", "replace")
        return {
            "status": "timeout",
            "execution_performed": True,
            "package_id": package_id,
            "software_query": software_query,
            "target_directory": target,
            "argv": argv[1:],
            "stdout": stdout_text[-12000:],
            "stderr": stderr_text[-12000:],
            "warning": "The installer may still have made partial changes before timeout.",
        }


def _verify_migration(
    query: str,
    target_directory: str,
) -> dict[str, Any]:
    target = _absolute_windows_path(target_directory, "target_directory")
    matches = _find_uninstall_matches(query)
    if not matches:
        return {
            "status": "failed",
            "verified": False,
            "reason": "The application is not registered as installed.",
            "target_directory": target,
        }
    if len(matches) != 1:
        return {
            "status": "inconclusive",
            "verified": False,
            "reason": "Multiple uninstall entries match the software query.",
            "target_directory": target,
        }

    entry = matches[0]
    actual = str(entry.get("InstallLocation") or "").strip()
    if not actual:
        return {
            "status": "inconclusive",
            "verified": False,
            "reason": "The installer did not publish an InstallLocation.",
            "target_directory": target,
            "version": entry.get("DisplayVersion"),
        }

    actual_norm = ntpath.normcase(ntpath.normpath(actual))
    target_norm = ntpath.normcase(ntpath.normpath(target))
    verified = (
        actual_norm == target_norm
        or actual_norm.startswith(target_norm + "\\")
    )
    return {
        "status": "passed" if verified else "failed",
        "verified": verified,
        "target_directory": target,
        "actual_install_location": actual,
        "version": entry.get("DisplayVersion"),
        "publisher": entry.get("Publisher"),
    }


@mcp.tool
def assess_software_migration(
    query: str,
    target_root: str = r"D:\Apps",
) -> dict[str, Any]:
    """Assess an installed Windows application for migration without changing the system."""
    return _assess_software_migration(query=query, target_root=target_root)


@mcp.tool
def preview_winget_reinstall(
    package_id: str,
    target_directory: str,
    source: str = "winget",
    silent: bool = True,
) -> dict[str, Any]:
    """Preview a WinGet reinstall command with a target location without executing it."""
    return _preview_winget_reinstall(
        package_id=package_id,
        target_directory=target_directory,
        source=source,
        silent=silent,
    )


@mcp.tool
def prepare_migration(
    query: str,
    package_id: str,
    target_directory: str,
    source: str = "winget",
) -> dict[str, Any]:
    """Build a controlled migration plan with an installation snapshot and rollback guidance."""
    return _prepare_migration(
        query=query,
        package_id=package_id,
        target_directory=target_directory,
        source=source,
    )




@mcp.tool
def execute_registered_uninstaller(
    software_query: str,
    extra_args: list[str] | None = None,
    timeout_seconds: int = 300,
    elevate: bool = False,
) -> dict[str, Any]:
    """Execute the software's own registered uninstaller with explicit extra args."""
    return _execute_registered_uninstaller(
        software_query=software_query,
        extra_args=extra_args,
        timeout_seconds=timeout_seconds,
        elevate=elevate,
    )



@mcp.tool
def elevation_broker_status() -> dict[str, Any]:
    """Read Interactive Elevation Broker liveness and desktop/session context."""
    return _elevation_broker_status()


@mcp.tool
def elevated_uninstall_status(
    launch_id: str,
) -> dict[str, Any]:
    """Read the status of one user-mediated elevated uninstall launch."""
    return _elevated_uninstall_status(launch_id)


@mcp.tool
def execute_elevated_winget_install(
    package_id: str,
    software_query: str,
    target_directory: str,
    source: str = "winget",
    silent: bool = True,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Queue one reviewed WinGet install through the Interactive Elevation Broker."""
    return _launch_elevated_winget_install(
        package_id=package_id,
        software_query=software_query,
        target_directory=target_directory,
        source=source,
        silent=silent,
        timeout_seconds=timeout_seconds,
    )


@mcp.tool
def elevated_install_status(
    launch_id: str,
) -> dict[str, Any]:
    """Read and semantically verify one broker-mediated WinGet install."""
    return _elevated_install_status(launch_id)


@mcp.tool
def execute_winget_uninstall(
    package_id: str,
    software_query: str,
    source: str = "winget",
    silent: bool = True,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Execute an exact WinGet uninstall after transaction/confirmation policy checks."""
    return _execute_winget_uninstall(
        package_id=package_id,
        software_query=software_query,
        source=source,
        silent=silent,
        timeout_seconds=timeout_seconds,
    )

@mcp.tool
def execute_winget_install(
    package_id: str,
    software_query: str,
    target_directory: str | None = None,
    source: str = "winget",
    silent: bool = True,
    require_absent: bool = True,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Execute a WinGet install, optionally with --location, after migration preconditions are met."""
    return _execute_winget_install(
        package_id=package_id,
        software_query=software_query,
        target_directory=target_directory,
        source=source,
        silent=silent,
        require_absent=require_absent,
        timeout_seconds=timeout_seconds,
    )


@mcp.tool
def verify_migration(
    query: str,
    target_directory: str,
) -> dict[str, Any]:
    """Verify that an installed application's registry location matches the migration target."""
    return _verify_migration(query=query, target_directory=target_directory)


if __name__ == "__main__":
    mcp.run()