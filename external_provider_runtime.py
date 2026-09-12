"""Production external-provider configuration for the PLA v1 capability runtime.

External providers are declared in provider_manifests/*.json. This module only
loads validated manifests, applies environment selection, creates stdio MCP
transports, and registers providers with the shared MCPClientManager.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastmcp.client.transports import StdioTransport

from mcp_client_manager import MCPClientManager
from provider_manifest import ProviderManifest, load_provider_manifests


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
) -> dict[str, Any]:
    transport = StdioTransport(
        command=str(manifest.command_path),
        args=list(manifest.args),
        cwd=str(manifest.cwd),
    )
    manager.add_provider(
        manifest.provider_id,
        transport,
        enabled=True,
        mode=manifest.mode,
        tool_allowlist=(
            list(manifest.tool_allowlist)
            if manifest.tool_allowlist is not None
            else None
        ),
        tool_overrides=manifest.tool_overrides,
    )
    return manifest.summary()


def configure_external_providers(
    manager: MCPClientManager,
    project_root: Path,
    *,
    manifest_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
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
