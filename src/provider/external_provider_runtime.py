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

from mcp_runtime.mcp_client_manager import MCPClientManager
from provider.provider_manifest import (
    ProviderManifest,
    STREAMABLE_HTTP,
    load_provider_manifests,
)
from provider.provider_setup_runtime import setup_provider_dependencies


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
            # MCP stdio intentionally inherits only a minimal OS environment.
            # Pass the desktop workspace context explicitly, never Tunnel/API
            # credentials or arbitrary developer provider settings.
            env={key: os.environ[key] for key in (
                'PLA_DESKTOP_RUNTIME', 'PLA_DATA_ROOT', 'PLA_INSTALL_RESOURCES',
                'PLA_RESOURCE_ROOT', 'AGENT_PLA_ROOT', 'AGENT_WORKSPACE', 'AGENT_WORKSPACES_CONFIG',
                'PLA_BROWSER_PORT') if key in os.environ}
            if os.environ.get('PLA_DESKTOP_RUNTIME') == '1' else None,
        )
    manager.add_provider(
        manifest.provider_id,
        transport,
        enabled=enabled,
        mode=manifest.mode,
        routing_authority=manifest.routing_authority,
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
        self._desktop_broker = None
        if os.environ.get("PLA_DESKTOP_RUNTIME") == "1":
            from desktop_runtime.broker_component import DesktopComponentBroker
            self._desktop_broker = DesktopComponentBroker(self._project_root, self._manifest_dir)

    def desktop_package_preview(self, **arguments) -> dict[str, Any]:
        if self._desktop_broker is None:
            raise ValueError("This installation is not a managed Desktop Runtime")
        return self._desktop_broker.package_preview(**arguments)

    def desktop_package_commit(self, **arguments) -> dict[str, Any]:
        if self._desktop_broker is None:
            raise ValueError("This installation is not a managed Desktop Runtime")
        provider_id = arguments.get("provider_id")
        active = self._manager.provider_status().get(provider_id)
        return self._desktop_broker.package_commit(
            **arguments, active=bool(active and active.get("enabled")))

    def desktop_install_status(self) -> dict[str, Any]:
        if self._desktop_broker is None:
            raise ValueError("This installation is not a managed Desktop Runtime")
        return self._desktop_broker.install_status()

    def setup_dependencies(self, provider_id: str, expected_sha256: str | None = None,
                           skill_package: str | None = None) -> dict[str, Any]:
        """Review or queue a digest-bound Desktop install; preserve source setup."""
        if self._desktop_broker is None:
            if skill_package is not None:
                raise ValueError("Skill source installation is available only in Desktop")
            return setup_provider_dependencies(self._project_root, provider_id)
        if expected_sha256 is None:
            return self._desktop_broker.install_preview(provider_id, skill_package)
        active = self._manager.provider_status()
        enabled = {name for name, details in active.items() if details.get("enabled")}
        return self._desktop_broker.install_start(provider_id, expected_sha256, enabled, skill_package)

    def import_manifest(self, content: str, confirm: bool = False, expected_sha256: str | None = None) -> dict[str, Any]:
        """Desktop-native plugin registration without opening private data roots."""
        if os.environ.get('PLA_DESKTOP_RUNTIME') != '1':
            raise ValueError('Managed manifest import is available in Desktop; source installations retain their manifest workflow')
        from desktop_runtime.components import ComponentProject
        project = ComponentProject(Path(os.environ['PLA_DATA_ROOT']), Path(os.environ['PLA_INSTALL_RESOURCES']))
        if project.root != self._project_root or project.manifest_dir != self._manifest_dir:
            raise ValueError('Desktop provider project does not match Runtime configuration')
        return project.import_manifest(content, confirm=confirm, expected_sha256=expected_sha256)

    def configure_manifest(self, **arguments) -> dict[str, Any]:
        if os.environ.get('PLA_DESKTOP_RUNTIME') != '1':
            raise ValueError('Managed provider configuration is available in Desktop')
        from desktop_runtime.components import ComponentProject
        project = ComponentProject(Path(os.environ['PLA_DATA_ROOT']), Path(os.environ['PLA_INSTALL_RESOURCES']))
        if project.root != self._project_root or project.manifest_dir != self._manifest_dir:
            raise ValueError('Desktop provider project does not match Runtime configuration')
        return project.configure_manifest(**arguments)

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

    def catalog(self) -> dict[str, Any]:
        """Read every validated manifest, including providers not yet enabled.

        Discovery/installation is never implied by a manifest's mere presence.
        This is the source of truth for control-panel inventory and new installs.
        """
        manifests = self._load_manifests()
        lifecycle = self._manager.provider_status()
        return {
            "manifest_directory": str(self._manifest_dir or self._project_root / "provider_manifests"),
            "providers": [
                {**manifest.summary(),
                 "configured": provider_id in self._active,
                 "lifecycle": lifecycle.get(provider_id),
                 "temporarily_enabled": provider_id in self._forced_enabled,
                 "temporarily_disabled": provider_id in self._forced_disabled}
                for provider_id, manifest in sorted(manifests.items())
            ],
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
