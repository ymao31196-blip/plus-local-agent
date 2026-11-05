"""Unified Office MCP entrypoint.

This provider composes the existing WPS (Node) and Office Enhancement (Python)
engines as isolated MCP subprocesses. It does not reimplement their tools.
An unavailable optional engine never prevents the other engine from registering.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.server.providers.proxy import ProxyProvider, _CacheEntry
from fastmcp.utilities.types import Image

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger("pla.office")
mcp = FastMCP("PLA Office", version="0.1.0")

_ENV_NAMES = (
    "PLA_DESKTOP_RUNTIME", "PLA_DATA_ROOT", "PLA_INSTALL_RESOURCES",
    "PLA_RESOURCE_ROOT", "AGENT_PLA_ROOT", "AGENT_WORKSPACE",
    "AGENT_WORKSPACES_CONFIG",
)
BACKEND_STATES: dict[str, dict[str, Any]] = {
    "wps": {"available": False, "last_discovery": "not_checked", "error": None},
    "enhancement": {"available": False, "last_discovery": "not_checked", "error": None},
}


def _backend_configurations(root: Path = ROOT) -> dict[str, dict[str, Any]]:
    """Use only the reviewed local executable locations, not arbitrary commands."""
    candidates: list[Path] = []
    if os.environ.get("PLA_DESKTOP_RUNTIME") == "1":
        # The packaged Desktop must use its reviewed Node runtime, never PATH.
        bundled = os.environ.get("PLA_INSTALL_RESOURCES")
        if bundled:
            candidates.append(Path(bundled) / "node.exe")
    else:
        candidates.append(root / "node.exe")
        node_in_path = shutil.which("node.exe") or shutil.which("node")
        if node_in_path:
            candidates.append(Path(node_in_path))
    node = next((str(p) for p in candidates if p.is_file()), None)
    wps = root / ".provider_sources" / "wps-office" / "wps-office-mcp"
    enhancement = root / "providers" / "office_enhancement_server.py"
    if os.name == "nt":
        python_path = root / ".provider_envs" / "office-enhancement" / "Scripts" / "python.exe"
    else:
        python_path = root / ".provider_envs" / "office-enhancement" / "bin" / "python"
    return {
        "wps": {
            "command": node,
            "args": [str(wps / "dist" / "index.js")],
            "cwd": str(wps),
            "available": bool(node and (wps / "dist" / "index.js").is_file()),
        },
        "enhancement": {
            "command": str(python_path),
            "args": [str(enhancement)],
            "cwd": str(root),
            "available": python_path.is_file() and enhancement.is_file(),
        },
    }


class IsolatedOfficeBackend(ProxyProvider):
    """Fail-open *for discovery only*: a missing backend adds zero tools."""

    def __init__(self, name: str, config: dict[str, Any]):
        self.backend_name = name
        self.config = config
        BACKEND_STATES[name]["available"] = bool(config["available"])

        def make_client() -> Client:
            if not config["available"]:
                raise RuntimeError(f"{name} backend prerequisites are missing")
            env = {key: os.environ[key] for key in _ENV_NAMES if key in os.environ}
            transport = StdioTransport(
                command=config["command"], args=list(config["args"]),
                cwd=config["cwd"], env=env or None,
            )
            return Client(transport)

        super().__init__(make_client, cache_ttl=15)

    async def _list_tools(self):
        try:
            if not self.config["available"]:
                raise RuntimeError(f"{self.backend_name} executable/source is absent")
            tools = await super()._list_tools()
            missing: list[str] = []
            if self.backend_name == "wps" and os.name == "nt":
                # The pinned upstream WPS tool registry advertises some actions
                # absent from its Windows COM bridge until our reviewed patch is
                # installed. Hide only known broken calls, not the entire engine.
                script = Path(self.config["cwd"]) / "scripts" / "wps-com.ps1"
                if script.is_file():
                    source = script.read_text(encoding="utf-8")
                    required = {
                        "wps_ppt_set_slide_size": '"setSlideSize" {',
                        "wps_ppt_set_font_color": '"setFontColor" {',
                        "wps_ppt_add_textbox": "name = $shape.Name; shapeName = $shape.Name; slideIndex = $slideIndex",
                    }
                    missing += [name for name, marker in required.items() if marker not in source]
                    if missing:
                        tools = [tool for tool in tools if tool.name not in missing]
            if self.backend_name == "enhancement" and os.environ.get("PLA_DESKTOP_RUNTIME") == "1":
                # Mirror the specialist server's Desktop-only dependency lookup.
                # Do not advertise an adapter that is guaranteed to fail at call time.
                artifact = ROOT / ".provider_envs" / "office-enhancement" / "node_modules" / "@oai" / "artifact-tool" / "package.json"
                if not artifact.is_file():
                    tools = [tool for tool in tools if tool.name != "artifact_build_overlay"]
                    missing.append("artifact_build_overlay")
            BACKEND_STATES[self.backend_name]["unavailable_tools"] = missing
            BACKEND_STATES[self.backend_name]["last_discovery"] = "ok"
            BACKEND_STATES[self.backend_name]["error"] = None
            return tools
        except Exception as exc:
            reason = f"{type(exc).__name__}: {str(exc)[:250]}"
            LOG.warning("%s backend unavailable: %s", self.backend_name, reason)
            BACKEND_STATES[self.backend_name]["last_discovery"] = "unavailable"
            BACKEND_STATES[self.backend_name]["error"] = reason
            self._tools_cache = _CacheEntry([], time.monotonic())
            return []


def _read_authorized_image(root: str, path: str) -> Image:
    """Use PLA's canonical workspace policy; do not accept arbitrary host paths."""
    if os.environ.get("PLA_DESKTOP_RUNTIME") == "1":
        policy_assets = Path(os.environ["PLA_INSTALL_RESOURCES"]) / "provider-assets" / "policy"
        if str(policy_assets) not in sys.path:
            sys.path.insert(0, str(policy_assets))
        from host.workspace_manager import build_root_policy, load_workspace_roots
        pla_root = Path(os.environ["AGENT_PLA_ROOT"])
        configured = load_workspace_roots(Path(os.environ["AGENT_WORKSPACES_CONFIG"]), pla_root)
        source = build_root_policy(
            Path(os.environ["AGENT_WORKSPACE"]), pla_root, configured
        ).resolve(root, path, "read").target
    else:
        source_root = ROOT / "src"
        if str(source_root) not in sys.path:
            sys.path.insert(0, str(source_root))
        from tooling.local_tools import safe_path
        source = safe_path(path, root, "read")

    suffix = source.suffix.casefold()
    if suffix not in {".png", ".jpg", ".jpeg"}:
        raise ValueError("Only PNG or JPEG preview images are supported")
    with source.open("rb") as handle:
        data = handle.read(5_000_001)
    if len(data) > 5_000_000:
        raise ValueError("Preview image exceeds the 5 MB transfer limit")
    if suffix == ".png" and not data.startswith(bytes((137, 80, 78, 71, 13, 10, 26, 10))):
        raise ValueError("Invalid PNG image")
    if suffix in {".jpg", ".jpeg"} and not data.startswith(bytes((255, 216))):
        raise ValueError("Invalid JPEG image")
    return Image(data=data, format="png" if suffix == ".png" else "jpeg")


def create_server(root: Path = ROOT) -> FastMCP:
    server = FastMCP("PLA Office", version="0.1.0")
    for name, config in _backend_configurations(root).items():
        server.add_provider(IsolatedOfficeBackend(name, config))

    @server.tool
    def office_preview_image(root: str, path: str) -> Image:
        """Return a rendered Office PNG/JPEG preview through MCP image content."""
        return _read_authorized_image(root, path)

    @server.tool
    def office_status() -> dict[str, Any]:
        """Report per-engine installation and discovery, not functional verification."""
        statuses = list(BACKEND_STATES.values())
        state = ("degraded" if any(
            item["last_discovery"] == "unavailable" or item.get("unavailable_tools")
            for item in statuses
        ) else "ready" if all(
            item["last_discovery"] == "ok" for item in statuses
        ) else "unverified")
        return {
            "status": state,
            "backends": {key: dict(value) for key, value in BACKEND_STATES.items()},
            "verification": "Tool discovery does not prove that an Office action works.",
        }

    return server


if __name__ == "__main__":
    create_server().run(transport="stdio")
