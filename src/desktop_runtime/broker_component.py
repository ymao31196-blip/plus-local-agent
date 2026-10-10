"""ChatGPT-native Desktop component review and installation bridge.

This is available only to the separately packaged Desktop Runtime. It exposes no
general-purpose shell or interpreter execution. Installation remains an explicit,
SHA-bound privileged operation and is never implicit in manifest registration.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Lock

from desktop_runtime.components import ComponentProject
from desktop_runtime.custom_packages import package_spec
from desktop_runtime.installer import ComponentInstaller, CORE_BUNDLE_IDS, core_bundle_plan


class DesktopComponentBroker:
    def __init__(self, project_root: Path, manifest_dir: Path):
        if os.environ.get("PLA_DESKTOP_RUNTIME") != "1":
            raise ValueError("Desktop component operations require the Desktop Runtime")
        data = Path(os.environ["PLA_DATA_ROOT"])
        resources = Path(os.environ["PLA_INSTALL_RESOURCES"])
        self.project = ComponentProject(data, resources)
        if self.project.root != project_root.resolve() or self.project.manifest_dir != manifest_dir.resolve():
            raise ValueError("Desktop component paths do not match the authenticated Runtime")
        self.installer = ComponentInstaller(data, resources)
        self._lock = Lock()
        self._process: subprocess.Popen | None = None
        self._job: str | None = None
        self._log: Path | None = None
        self._plan: dict | None = None
        self._install_gate = None
        atexit.register(self._stop_children)

    def package_preview(self, provider_id: str, package_kind: str, packages: list[str]) -> dict:
        return package_spec(self.project, provider_id, package_kind, packages)

    def package_commit(self, provider_id: str, package_kind: str, packages: list[str],
                       expected_sha256: str, active: bool = False) -> dict:
        if active:
            raise ValueError("Disable this Provider before changing installed dependencies")
        return package_spec(self.project, provider_id, package_kind, packages,
                            confirmed=True, expected_sha256=expected_sha256)

    @staticmethod
    def _digest(plan: dict) -> str:
        return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()

    def install_preview(self, provider_id: str, skill_package: str | None = None) -> dict:
        if provider_id == "starter-pack":
            if skill_package:
                raise ValueError("Starter-pack installation never accepts a user-supplied package")
            plan = core_bundle_plan(self.installer)
        else:
            plan = self.installer.plan(provider_id, skill_package)
        return {**plan, "sha256": self._digest(plan), "started": False,
                "mcp_connection_verified": False, "installation_is_activation": False}

    def _command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "component_install"]
        return [sys.executable, str(Path(__file__).with_name("entry.py")), "component_install"]

    def install_start(self, provider_id: str, expected_sha256: str, enabled_providers: set[str],
                      skill_package: str | None = None) -> dict:
        with self._lock:
            plan = self.install_preview(provider_id, skill_package)
            if plan["sha256"] != expected_sha256:
                raise ValueError("Installation plan changed; preview again")
            items = CORE_BUNDLE_IDS if provider_id == "starter-pack" else (provider_id,)
            if any(item in enabled_providers for item in items):
                raise ValueError("Disable the selected Provider before updating dependencies")
            if self._process is not None and self._process.poll() is None:
                raise ValueError("A Desktop component installation is already running")
            if self._install_gate is not None:
                self._install_gate.release()
                self._install_gate = None
            from desktop_runtime.component_lock import ComponentInstallLock
            ownership = ComponentInstallLock(self.project.data)
            ownership.acquire()
            log = self.project.data / "logs" / "broker-component-install.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            command = self._command() + [
                "--data-dir", str(self.project.data),
                "--resources", str(self.project.resources),
                "--expected-plan-sha256", expected_sha256,
            ]
            command += ["--bundle"] if provider_id == "starter-pack" else ["--provider-id", provider_id]
            if skill_package:
                command += ["--skill-package", str(plan["skill_package"])]
            # The runtime was started by Desktop using a sanitized environment.
            environment = {key: val for key, val in os.environ.items()
                           if not key.startswith(("PIP_", "UV_", "PYTHON", "VIRTUAL_ENV", "CONDA_"))}
            environment["PYTHONUNBUFFERED"] = "1"
            if not getattr(sys, "frozen", False):
                # Source-mode acceptance needs this trusted repository src root.
                # Never inherit arbitrary caller PYTHONPATH.
                environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
            try:
                with log.open("wb") as stream:
                    proc = subprocess.Popen(
                        command, cwd=self.project.data, env=environment,
                        stdout=stream, stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
            except BaseException:
                ownership.release()
                raise
            self._install_gate = ownership
            self._process = proc
            self._job = provider_id
            self._plan = plan
            self._log = log
            return {"status": "started", "provider_id": provider_id, "job": "desktop:" + provider_id,
                    "pid": proc.pid, "sha256": expected_sha256, "activated": False}

    def install_status(self) -> dict:
        with self._lock:
            process = self._process
            if process is None:
                return {"state": "idle", "job": None, "installed_receipts": self._receipts()}
            code = process.poll()
            if code is not None and self._install_gate is not None:
                self._install_gate.release()
                self._install_gate = None
            name = self._job
            items = CORE_BUNDLE_IDS if name == "starter-pack" else (name,)
            installed = code == 0 and all(
                (self.project.root / "receipts" / f"{item}.json").is_file()
                for item in items if item != "browser"
            )
            if code == 0 and name == "browser":
                installed = True
            raw = self._log.read_bytes()[-16000:] if self._log and self._log.exists() else b""
            return {"job": "desktop:" + name, "provider_id": name, "pid": process.pid,
                    "exit_code": code, "state": "running" if code is None else
                    "installed" if installed else "failed", "installed_receipts": self._receipts(),
                    "logs_tail": raw.decode("utf-8", errors="replace"),
                    "activated": False}

    def _receipts(self) -> list[str]:
        folder = self.project.root / "receipts"
        return sorted(path.stem for path in folder.glob("*.json")) if folder.is_dir() else []

    def _stop_children(self):
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                from runtime.runtime_context import terminate_owned_process_tree
                terminate_owned_process_tree(process)
            if self._install_gate is not None:
                self._install_gate.release()
                self._install_gate = None
