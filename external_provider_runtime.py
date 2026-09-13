"""Production external-provider configuration and hot-plug runtime for PLA v1.

External providers are declared in provider_manifests/*.json. This module loads
validated manifests, applies environment selection, creates stdio MCP transports,
and keeps the shared MCPClientManager synchronized without restarting PLA HTTP.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

from mcp_client_manager import MCPClientManager
from provider_manifest import (
    ProviderManifest,
    STREAMABLE_HTTP,
    load_provider_manifests,
)


def _selected_provider_ids(
    manifests: dict[str, ProviderManifest],
) -> set[str]:
    raw = os.environ.get("PLA_EXTERNAL_PROVIDERS")
    if raw is None or not raw.strip():
        return set()

    raw = raw.strip()
    if raw == "*":
        return {
            provider_id
            for provider_id, manifest in manifests.items()
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
            "Unknown external provider ids: " + ", ".join(unknown)
        )
    return selected


def _register_manifest(
    manager: MCPClientManager,
    manifest: ProviderManifest,
    *,
    enabled: bool = True,
) -> dict[str, Any]:
    if manifest.runtime_kind == STREAMABLE_HTTP:
        if manifest.endpoint_url is None:
            raise ValueError(
                f"HTTP provider {manifest.provider_id} is missing endpoint_url"
            )
        transport = StreamableHttpTransport(manifest.endpoint_url)
    else:
        if manifest.command_path is None:
            raise ValueError(
                f"stdio provider {manifest.provider_id} is missing command_path"
            )
        transport = StdioTransport(
            command=str(manifest.command_path),
            args=list(manifest.args),
            cwd=str(manifest.cwd),
        )
    manager.add_provider(
        manifest.provider_id,
        transport,
        enabled=enabled,
        mode=manifest.mode,
        tool_allowlist=(
            list(manifest.tool_allowlist)
            if manifest.tool_allowlist is not None
            else None
        ),
        tool_overrides=manifest.tool_overrides,
        discovery_timeout=manifest.discovery_timeout_seconds,
        invoke_timeout=manifest.invoke_timeout_seconds,
        persistent_session=manifest.persistent_session,
    )
    value = manifest.summary()
    value["enabled"] = bool(enabled)
    return value


def configure_external_providers(
    manager: MCPClientManager,
    project_root: Path,
    *,
    manifest_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Compatibility helper for one-shot startup configuration."""
    manifests = load_provider_manifests(
        project_root,
        manifest_dir=manifest_dir,
    )
    selected = _selected_provider_ids(manifests)

    configured: dict[str, dict[str, Any]] = {}
    for provider_id in sorted(selected):
        configured[provider_id] = _register_manifest(
            manager,
            manifests[provider_id],
        )
    return configured


class ExternalProviderRuntime:
    """Synchronize manifest-backed MCP providers without restarting PLA HTTP.

    Hot-plug state is intentionally in-memory only. Manifests and
    PLA_EXTERNAL_PROVIDERS remain the persistent source of truth after restart.
    """

    def __init__(
        self,
        manager: MCPClientManager,
        project_root: Path,
        *,
        manifest_dir: Path | None = None,
    ) -> None:
        self._manager = manager
        self._project_root = project_root.resolve()
        self._manifest_dir = (
            manifest_dir.resolve()
            if manifest_dir is not None
            else None
        )
        self._active: dict[str, ProviderManifest] = {}
        self._forced_enabled: set[str] = set()
        self._forced_disabled: set[str] = set()
        self._lock = asyncio.Lock()

    def _load_manifests(self) -> dict[str, ProviderManifest]:
        return load_provider_manifests(
            self._project_root,
            manifest_dir=self._manifest_dir,
        )

    def _base_selected(
        self,
        manifests: dict[str, ProviderManifest],
    ) -> set[str]:
        return _selected_provider_ids(manifests)

    def _desired_provider_ids(
        self,
        manifests: dict[str, ProviderManifest],
    ) -> set[str]:
        # Forget ephemeral overrides for manifests that no longer exist.
        self._forced_enabled.intersection_update(manifests)
        self._forced_disabled.intersection_update(manifests)
        return self._base_selected(manifests) | self._forced_enabled

    def configure_initial(self) -> dict[str, dict[str, Any]]:
        manifests = self._load_manifests()
        selected = self._base_selected(manifests)
        configured: dict[str, dict[str, Any]] = {}
        for provider_id in sorted(selected):
            manifest = manifests[provider_id]
            configured[provider_id] = _register_manifest(
                self._manager,
                manifest,
                enabled=True,
            )
            self._active[provider_id] = manifest
        return configured

    def status(self) -> dict[str, Any]:
        return {
            "active_provider_ids": sorted(self._active),
            "forced_enabled": sorted(self._forced_enabled),
            "forced_disabled": sorted(self._forced_disabled),
            "providers": self._manager.provider_status(),
        }

    async def rescan(self) -> dict[str, Any]:
        """Reload the manifest directory and apply add/change/remove differences."""
        async with self._lock:
            # Parsing and selection happen before any mutation, so one invalid
            # manifest cannot partially reconfigure the current runtime.
            manifests = self._load_manifests()
            desired = self._desired_provider_ids(manifests)

            previous_ids = set(self._active)
            removed = sorted(previous_ids - desired)
            added = sorted(desired - previous_ids)
            changed = sorted(
                provider_id
                for provider_id in desired & previous_ids
                if self._active[provider_id] != manifests[provider_id]
            )
            unchanged = sorted(
                (desired & previous_ids) - set(changed)
            )

            for provider_id in removed:
                if self._manager.has_provider(provider_id):
                    await self._manager.close_provider_session(provider_id)
                    self._manager.remove_provider(provider_id)
                self._active.pop(provider_id, None)

            discovery: dict[str, dict[str, Any]] = {}
            errors: dict[str, dict[str, str]] = {}

            for provider_id in sorted(set(added) | set(changed)):
                manifest = manifests[provider_id]
                enabled = provider_id not in self._forced_disabled
                if self._manager.has_provider(provider_id):
                    await self._manager.close_provider_session(provider_id)
                _register_manifest(
                    self._manager,
                    manifest,
                    enabled=enabled,
                )
                self._active[provider_id] = manifest
                if not enabled:
                    discovery[provider_id] = self._manager.provider_status(
                        provider_id
                    )
                    continue
                try:
                    discovery[provider_id] = (
                        await self._manager.discover_provider(
                            provider_id,
                            force=True,
                        )
                    )
                except Exception as exc:
                    discovery[provider_id] = self._manager.provider_status(
                        provider_id
                    )
                    errors[provider_id] = {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }

            for provider_id in unchanged:
                enabled = provider_id not in self._forced_disabled
                state = self._manager.provider_status(provider_id)
                if state["enabled"] != enabled:
                    if not enabled:
                        await self._manager.close_provider_session(provider_id)
                    self._manager.set_provider_enabled(
                        provider_id,
                        enabled,
                    )
                    state = self._manager.provider_status(provider_id)

                # Rescan also recovers an unchanged selected provider whose
                # transport previously failed, without disturbing ready ones.
                if enabled and state["state"] != "ready":
                    try:
                        state = await self._manager.discover_provider(
                            provider_id,
                            force=True,
                        )
                    except Exception as exc:
                        state = self._manager.provider_status(provider_id)
                        errors[provider_id] = {
                            "type": type(exc).__name__,
                            "message": str(exc),
                        }
                discovery[provider_id] = state

            return {
                "status": "completed" if not errors else "partial",
                "added": added,
                "changed": changed,
                "removed": removed,
                "unchanged": unchanged,
                "errors": errors,
                "runtime": self.status(),
            }

    async def reload(self, provider_id: str) -> dict[str, Any]:
        """Reload one already-selected/configured provider from its manifest."""
        async with self._lock:
            manifests = self._load_manifests()
            if provider_id not in manifests:
                raise ValueError(
                    f"Unknown provider manifest: {provider_id}"
                )

            selected = self._base_selected(manifests)
            if (
                provider_id not in self._active
                and provider_id not in selected
                and provider_id not in self._forced_enabled
            ):
                raise ValueError(
                    f"Provider is not selected/configured: {provider_id}; "
                    "enable it first or make it autostart/selected"
                )

            manifest = manifests[provider_id]
            enabled = provider_id not in self._forced_disabled
            if self._manager.has_provider(provider_id):
                await self._manager.close_provider_session(provider_id)
            _register_manifest(
                self._manager,
                manifest,
                enabled=enabled,
            )
            self._active[provider_id] = manifest

            if enabled:
                state = await self._manager.discover_provider(
                    provider_id,
                    force=True,
                )
            else:
                state = self._manager.provider_status(provider_id)

            return {
                "status": "completed",
                "provider_id": provider_id,
                "provider": state,
            }

    async def enable(self, provider_id: str) -> dict[str, Any]:
        """Enable a manifest-backed provider for the current runtime session."""
        async with self._lock:
            manifests = self._load_manifests()
            if provider_id not in manifests:
                raise ValueError(
                    f"Unknown provider manifest: {provider_id}"
                )

            self._forced_disabled.discard(provider_id)
            self._forced_enabled.add(provider_id)
            manifest = manifests[provider_id]

            if (
                provider_id not in self._active
                or self._active[provider_id] != manifest
                or not self._manager.has_provider(provider_id)
            ):
                if self._manager.has_provider(provider_id):
                    await self._manager.close_provider_session(provider_id)
                _register_manifest(
                    self._manager,
                    manifest,
                    enabled=True,
                )
                self._active[provider_id] = manifest
            else:
                self._manager.set_provider_enabled(provider_id, True)

            state = self._manager.provider_status(provider_id)
            if state["state"] != "ready":
                state = await self._manager.discover_provider(
                    provider_id,
                    force=True,
                )

            return {
                "status": "completed",
                "provider_id": provider_id,
                "provider": state,
            }

    async def disable(self, provider_id: str) -> dict[str, Any]:
        """Disable an active provider for the current runtime session."""
        async with self._lock:
            if provider_id not in self._active:
                raise ValueError(
                    f"Provider is not active/configured: {provider_id}"
                )
            self._forced_enabled.discard(provider_id)
            self._forced_disabled.add(provider_id)
            await self._manager.close_provider_session(provider_id)
            self._manager.set_provider_enabled(provider_id, False)
            return {
                "status": "completed",
                "provider_id": provider_id,
                "provider": self._manager.provider_status(provider_id),
            }
