"""Manifest-backed external Observer plugin runtime for PLA v1.2 Phase 4."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from threading import RLock
from typing import Any, Callable

from observer_hook_runtime import ObserverHookRuntime
from observer_plugin_manifest import (
    ExternalObserverManifest,
    load_external_observer_manifests,
)


MAX_EXTERNAL_OBSERVERS = 16
MAX_PLUGIN_STDOUT_BYTES = 64 * 1024
_SAFE_ENV_NAMES = (
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "PATH",
    "PATHEXT",
    "COMSPEC",
)


def _selected_observer_ids(
    manifests: dict[str, ExternalObserverManifest],
) -> set[str]:
    raw = os.environ.get("PLA_EXTERNAL_OBSERVERS")
    if raw is None or not raw.strip():
        return set()
    raw = raw.strip()
    if raw == "*":
        return {
            observer_id
            for observer_id, manifest in manifests.items()
            if manifest.autostart
        }
    selected = {
        item.strip().casefold()
        for item in raw.split(",")
        if item.strip()
    }
    unknown = sorted(selected - set(manifests))
    if unknown:
        raise ValueError(
            "Unknown external observer ids: " + ", ".join(unknown)
        )
    return selected


def _safe_subprocess_env() -> dict[str, str]:
    env = {
        name: os.environ[name]
        for name in _SAFE_ENV_NAMES
        if name in os.environ
    }
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def invoke_external_observer(
    manifest: ExternalObserverManifest,
    event: dict[str, Any],
) -> dict[str, Any]:
    """Invoke one isolated Python observer using one JSON object on stdin/stdout."""
    if not manifest.python_path.is_file():
        raise RuntimeError(
            f"External observer Python is unavailable: {manifest.observer_id}"
        )
    if not manifest.cwd.is_dir():
        raise RuntimeError(
            f"External observer cwd is unavailable: {manifest.observer_id}"
        )

    payload = json.dumps(
        event,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        completed = subprocess.run(
            [
                str(manifest.python_path),
                "-m",
                manifest.module,
            ],
            cwd=str(manifest.cwd),
            input=payload,
            text=True,
            encoding="utf-8",
            errors="strict",
            capture_output=True,
            timeout=manifest.timeout_seconds,
            env=_safe_subprocess_env(),
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"External observer timed out: {manifest.observer_id}"
        ) from exc

    stderr = completed.stderr or ""
    if completed.returncode != 0:
        stderr_bytes = stderr.encode("utf-8", errors="replace")
        raise RuntimeError(
            f"External observer exited with code {completed.returncode}; "
            f"stderr_sha256={hashlib.sha256(stderr_bytes).hexdigest()}; "
            f"stderr_length={len(stderr)}"
        )

    stdout = completed.stdout or ""
    stdout_bytes = stdout.encode("utf-8", errors="strict")
    if len(stdout_bytes) > MAX_PLUGIN_STDOUT_BYTES:
        raise RuntimeError(
            f"External observer output exceeded {MAX_PLUGIN_STDOUT_BYTES} bytes"
        )
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"External observer returned invalid JSON: {manifest.observer_id}"
        ) from exc
    if not isinstance(result, dict):
        raise RuntimeError(
            f"External observer result must be an object: {manifest.observer_id}"
        )
    return result


class ExternalObserverRuntime:
    """Synchronize reviewed external Observer manifests without HTTP restart."""

    def __init__(
        self,
        hooks: ObserverHookRuntime,
        project_root: Path,
        *,
        manifest_dir: Path | None = None,
        runner: Callable[[ExternalObserverManifest, dict[str, Any]], Any] | None = None,
    ) -> None:
        self._hooks = hooks
        self._project_root = project_root.resolve()
        self._manifest_dir = (
            manifest_dir.resolve() if manifest_dir is not None else None
        )
        self._runner = runner or invoke_external_observer
        self._active: dict[str, ExternalObserverManifest] = {}
        self._forced_enabled: set[str] = set()
        self._forced_disabled: set[str] = set()
        self._lock = RLock()

    def _load_manifests(self) -> dict[str, ExternalObserverManifest]:
        return load_external_observer_manifests(
            self._project_root,
            manifest_dir=self._manifest_dir,
        )

    def _base_selected(
        self,
        manifests: dict[str, ExternalObserverManifest],
    ) -> set[str]:
        return _selected_observer_ids(manifests)

    def _desired_ids(
        self,
        manifests: dict[str, ExternalObserverManifest],
    ) -> set[str]:
        self._forced_enabled.intersection_update(manifests)
        self._forced_disabled.intersection_update(manifests)
        return self._base_selected(manifests) | self._forced_enabled

    @staticmethod
    def _ensure_ready(manifest: ExternalObserverManifest) -> None:
        if not manifest.python_path.is_file():
            raise ValueError(
                f"Observer Python does not exist: {manifest.python_path}"
            )
        if not manifest.cwd.is_dir():
            raise ValueError(f"Observer cwd does not exist: {manifest.cwd}")

    def _handler(
        self,
        manifest: ExternalObserverManifest,
    ) -> Callable[[dict[str, Any]], Any]:
        def handler(event: dict[str, Any]) -> Any:
            return self._runner(manifest, event)
        return handler

    def _register(
        self,
        manifest: ExternalObserverManifest,
        *,
        enabled: bool,
    ) -> None:
        self._ensure_ready(manifest)
        self._hooks.register(
            manifest.hook_id,
            manifest.event_types,
            self._handler(manifest),
            enabled=enabled,
        )

    def configure_initial(self) -> dict[str, dict[str, Any]]:
        manifests = self._load_manifests()
        selected = self._base_selected(manifests)
        if len(selected) > MAX_EXTERNAL_OBSERVERS:
            raise ValueError(
                f"At most {MAX_EXTERNAL_OBSERVERS} external observers may be active"
            )
        configured: dict[str, dict[str, Any]] = {}
        for observer_id in sorted(selected):
            manifest = manifests[observer_id]
            self._register(manifest, enabled=True)
            self._active[observer_id] = manifest
            configured[observer_id] = manifest.summary()
        return configured

    def status(self) -> dict[str, Any]:
        hooks = self._hooks.status()["hooks"]
        external_hooks = [
            item for item in hooks
            if str(item.get("hook_id", "")).startswith("external.")
        ]
        return {
            "status": "ready",
            "active_observer_ids": sorted(self._active),
            "forced_enabled": sorted(self._forced_enabled),
            "forced_disabled": sorted(self._forced_disabled),
            "observers": {
                observer_id: manifest.summary()
                for observer_id, manifest in sorted(self._active.items())
            },
            "hooks": external_hooks,
        }

    def rescan(self) -> dict[str, Any]:
        with self._lock:
            manifests = self._load_manifests()
            desired = self._desired_ids(manifests)
            if len(desired) > MAX_EXTERNAL_OBSERVERS:
                raise ValueError(
                    f"At most {MAX_EXTERNAL_OBSERVERS} external observers may be active"
                )
            for observer_id in desired:
                self._ensure_ready(manifests[observer_id])

            previous_ids = set(self._active)
            removed = sorted(previous_ids - desired)
            added = sorted(desired - previous_ids)
            changed = sorted(
                observer_id
                for observer_id in desired & previous_ids
                if self._active[observer_id] != manifests[observer_id]
            )
            unchanged = sorted(
                (desired & previous_ids) - set(changed)
            )

            for observer_id in removed:
                self._hooks.unregister(self._active[observer_id].hook_id)
                self._active.pop(observer_id, None)

            for observer_id in changed:
                old = self._active[observer_id]
                self._hooks.unregister(old.hook_id)
                manifest = manifests[observer_id]
                enabled = observer_id not in self._forced_disabled
                self._register(manifest, enabled=enabled)
                self._active[observer_id] = manifest

            for observer_id in added:
                manifest = manifests[observer_id]
                enabled = observer_id not in self._forced_disabled
                self._register(manifest, enabled=enabled)
                self._active[observer_id] = manifest

            return {
                "status": "completed",
                "added": added,
                "changed": changed,
                "removed": removed,
                "unchanged": unchanged,
                "runtime": self.status(),
            }

    def reload(self, observer_id: str) -> dict[str, Any]:
        observer_id = observer_id.strip().casefold()
        with self._lock:
            if observer_id not in self._active:
                raise ValueError(
                    f"Observer is not active/configured: {observer_id}"
                )
            manifests = self._load_manifests()
            if observer_id not in manifests:
                raise ValueError(f"Observer manifest not found: {observer_id}")
            manifest = manifests[observer_id]
            self._ensure_ready(manifest)
            old = self._active[observer_id]
            self._hooks.unregister(old.hook_id)
            enabled = observer_id not in self._forced_disabled
            self._register(manifest, enabled=enabled)
            self._active[observer_id] = manifest
            return {
                "status": "completed",
                "observer_id": observer_id,
                "observer": manifest.summary(),
            }

    def enable(self, observer_id: str) -> dict[str, Any]:
        observer_id = observer_id.strip().casefold()
        with self._lock:
            manifests = self._load_manifests()
            if observer_id not in manifests:
                raise ValueError(f"Observer manifest not found: {observer_id}")
            self._forced_disabled.discard(observer_id)
            self._forced_enabled.add(observer_id)
            manifest = manifests[observer_id]
            self._ensure_ready(manifest)
            if observer_id not in self._active:
                if len(self._active) >= MAX_EXTERNAL_OBSERVERS:
                    raise ValueError(
                        f"At most {MAX_EXTERNAL_OBSERVERS} external observers may be active"
                    )
                self._register(manifest, enabled=True)
                self._active[observer_id] = manifest
            else:
                if self._active[observer_id] != manifest:
                    old = self._active[observer_id]
                    self._hooks.unregister(old.hook_id)
                    self._register(manifest, enabled=True)
                    self._active[observer_id] = manifest
                else:
                    self._hooks.set_enabled(manifest.hook_id, True)
            return {
                "status": "completed",
                "observer_id": observer_id,
                "runtime": self.status(),
            }

    def disable(self, observer_id: str) -> dict[str, Any]:
        observer_id = observer_id.strip().casefold()
        with self._lock:
            if observer_id not in self._active:
                raise ValueError(
                    f"Observer is not active/configured: {observer_id}"
                )
            self._forced_enabled.discard(observer_id)
            self._forced_disabled.add(observer_id)
            self._hooks.set_enabled(
                self._active[observer_id].hook_id,
                False,
            )
            return {
                "status": "completed",
                "observer_id": observer_id,
                "runtime": self.status(),
            }
