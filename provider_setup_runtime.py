from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


_PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def setup_provider_dependencies(
    project_root: Path,
    provider_id: str,
    *,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    provider = str(provider_id).strip().casefold()
    if not _PROVIDER_ID.fullmatch(provider):
        raise ValueError("provider_id is invalid")
    if not isinstance(timeout_seconds, int) or not 30 <= timeout_seconds <= 600:
        raise ValueError("timeout_seconds must be between 30 and 600")

    spec_dir = root / "provider_specs"
    python_spec = spec_dir / f"{provider}.txt"
    node_spec = spec_dir / f"{provider}.npm.txt"
    if not python_spec.is_file() and not node_spec.is_file():
        raise ValueError(
            f"No reviewed dependency spec exists for provider: {provider}"
        )

    script = (root / "setup_providers.ps1").resolve()
    try:
        script.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("setup_providers.ps1 escaped PLA root") from exc
    if not script.is_file():
        raise RuntimeError("setup_providers.ps1 is missing")

    powershell = shutil.which("powershell.exe")
    if not powershell:
        raise RuntimeError("powershell.exe was not found")

    argv = [
        str(Path(powershell).resolve()),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Provider",
        provider,
    ]
    completed = subprocess.run(
        argv,
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        shell=False,
        check=False,
    )
    stdout = completed.stdout[-20000:]
    stderr = completed.stderr[-20000:]
    if completed.returncode != 0:
        raise RuntimeError(
            stderr.strip()
            or stdout.strip()
            or f"Provider setup failed with exit code {completed.returncode}"
        )
    return {
        "status": "completed",
        "provider_id": provider,
        "python_spec": python_spec.is_file(),
        "node_spec": node_spec.is_file(),
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": len(completed.stdout) > len(stdout),
        "stderr_truncated": len(completed.stderr) > len(stderr),
    }
