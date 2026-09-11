"""Provider lifecycle diagnostics for PLA v0.18."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from mcp_client_manager import MCPClientManager
from provider_manifest import ProviderManifest, load_provider_manifests


_EXACT_PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)$")


def _read_expected_versions(spec_path: Path) -> tuple[dict[str, str], list[str]]:
    expected: dict[str, str] = {}
    unverifiable: list[str] = []
    if not spec_path.is_file():
        return expected, unverifiable
    for raw_line in spec_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _EXACT_PIN.fullmatch(line)
        if match:
            expected[match.group(1)] = match.group(2)
        else:
            unverifiable.append(line)
    return expected, unverifiable


def _installed_versions(
    python_path: Path,
    package_names: list[str],
) -> tuple[dict[str, str | None], str | None]:
    if not python_path.is_file():
        return {}, "provider Python interpreter is missing"
    if not package_names:
        return {}, None

    script = (
        "import importlib.metadata as m, json, sys\n"
        "names=json.loads(sys.argv[1])\n"
        "out={}\n"
        "for name in names:\n"
        "    try: out[name]=m.version(name)\n"
        "    except m.PackageNotFoundError: out[name]=None\n"
        "print(json.dumps(out, sort_keys=True))\n"
    )
    try:
        completed = subprocess.run(
            [
                str(python_path),
                "-c",
                script,
                json.dumps(package_names),
            ],
            cwd=str(python_path.parent),
            shell=False,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"

    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        return {}, f"version probe exited {completed.returncode}: {message[:1000]}"
    try:
        payload = json.loads(completed.stdout.strip())
    except json.JSONDecodeError:
        return {}, "version probe returned invalid JSON"
    if not isinstance(payload, dict):
        return {}, "version probe returned a non-object"
    return {str(k): (None if v is None else str(v)) for k, v in payload.items()}, None


def _manifest_static_checks(
    manifest: ProviderManifest,
    project_root: Path,
) -> dict[str, Any]:
    spec_path = project_root / "provider_specs" / f"{manifest.provider_id}.txt"
    expected, unverifiable = _read_expected_versions(spec_path)
    installed, version_probe_error = _installed_versions(
        manifest.python_path,
        sorted(expected),
    )
    drift = {
        package: {
            "expected": version,
            "installed": installed.get(package),
            "match": installed.get(package) == version,
        }
        for package, version in sorted(expected.items())
    }
    drift_detected = any(not item["match"] for item in drift.values())

    return {
        "manifest": str(manifest.path),
        "manifest_exists": manifest.path.is_file(),
        "spec": str(spec_path),
        "spec_exists": spec_path.is_file(),
        "python": str(manifest.python_path),
        "python_exists": manifest.python_path.is_file(),
        "cwd": str(manifest.cwd),
        "cwd_exists": manifest.cwd.is_dir(),
        "mode": manifest.mode,
        "autostart": manifest.autostart,
        "tool_allowlist": (
            list(manifest.tool_allowlist)
            if manifest.tool_allowlist is not None
            else None
        ),
        "expected_versions": expected,
        "installed_versions": installed,
        "version_drift": drift,
        "version_drift_detected": drift_detected,
        "unverifiable_spec_lines": unverifiable,
        "version_probe_error": version_probe_error,
    }


async def provider_doctor(
    manager: MCPClientManager,
    project_root: Path,
    *,
    provider_id: str | None = None,
    live_probe: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    manifests = load_provider_manifests(project_root)
    current_status = manager.provider_status()
    configured_ids = set(current_status)

    if provider_id is not None:
        if provider_id not in configured_ids:
            raise ValueError(f"Unknown configured provider: {provider_id}")
        selected = [provider_id]
    else:
        selected = sorted(configured_ids)

    results: list[dict[str, Any]] = []
    for current_id in selected:
        manifest = manifests.get(current_id)
        if manifest is None:
            static = {
                "manifest_exists": False,
                "spec_exists": False,
                "python_exists": False,
                "cwd_exists": False,
                "version_drift_detected": True,
                "version_probe_error": "configured provider has no manifest",
            }
        else:
            static = _manifest_static_checks(manifest, project_root)

        probe: dict[str, Any] = {"attempted": False}
        if live_probe:
            probe["attempted"] = True
            try:
                state = await manager.discover_provider(
                    current_id,
                    force=force,
                )
                probe.update({
                    "ok": True,
                    "state": state,
                })
            except Exception as exc:
                probe.update({
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "state": manager.provider_status(current_id),
                })

        state = manager.provider_status(current_id)
        static_ok = all(
            bool(static.get(key))
            for key in (
                "manifest_exists",
                "spec_exists",
                "python_exists",
                "cwd_exists",
            )
        ) and not bool(static.get("version_drift_detected")) and not static.get(
            "version_probe_error"
        )
        if not state["enabled"]:
            status = "disabled"
        else:
            lifecycle_ok = state["state"] == "ready"
            status = "healthy" if static_ok and lifecycle_ok else "degraded"

        results.append({
            "provider_id": current_id,
            "status": status,
            "lifecycle": state,
            "static": static,
            "live_probe": probe,
        })

    healthy = sum(item["status"] == "healthy" for item in results)
    disabled = sum(item["status"] == "disabled" for item in results)
    degraded = sum(item["status"] == "degraded" for item in results)
    overall = (
        "empty"
        if not results
        else ("healthy" if degraded == 0 else "degraded")
    )
    return {
        "status": overall,
        "provider_count": len(results),
        "healthy_count": healthy,
        "disabled_count": disabled,
        "degraded_count": degraded,
        "providers": results,
    }
