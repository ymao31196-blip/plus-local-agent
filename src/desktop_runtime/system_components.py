"""Read-only discovery of Microsoft's WinGet MCP shipped with App Installer.

Never downloads or executes a substitute binary. The MSIX app-owned executable
is resolved only under the current user's WindowsApps package family directory.
"""
from __future__ import annotations
import os
from pathlib import Path

APP_INSTALLER_FAMILY = "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe"
MCP_EXECUTABLE = "WindowsPackageManagerMCPServer.exe"


def official_winget_mcp(local_appdata: Path | None = None) -> Path | None:
    if os.name != "nt" and local_appdata is None:
        return None
    if local_appdata is None:
        raw = os.environ.get("LOCALAPPDATA")
        local_appdata = Path(raw) if raw else Path.home() / "AppData" / "Local"
    local_appdata = Path(local_appdata)
    if not local_appdata.is_absolute():
        return None
    candidate = (local_appdata / "Microsoft" / "WindowsApps" /
                 APP_INSTALLER_FAMILY / MCP_EXECUTABLE)
    # Do not trust PATH, a downloaded copy, or an unreviewed local MCP.
    return candidate.resolve() if candidate.is_file() else None
