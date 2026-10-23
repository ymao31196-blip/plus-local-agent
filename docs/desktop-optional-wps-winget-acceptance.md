# Desktop optional MCP completion — WPS and WinGet

Validated on 2026-10-10 using actual PLA native resources with separate, temporary Desktop private-data roots. The currently installed Desktop data was not modified by the source-level verification.

## WPS Office

The packaged Node executable is not a complete installation. WPS uses the reviewed git/npm source specification `provider_specs/wps-office.source.json`, which pins repository revision `a82533662268b3245f93d8685bc45dffede048b6` and a Windows COM compatibility patch. An isolated install via the existing Desktop installer completed successfully; the workdir was present, a live provider discovered **250 tools**, and real read-only `wps_common_ping` and `wps_check_connection` calls succeeded. The server returned `WPS连接正常！pong` and `connected=true`. No WPS document or OS software was changed. Reports: `.desktop-build/rc7-wps-isolated-real.json` and `.desktop-build/rc8-wps-readonly-e2e.json`.

The currently installed RC.7 user profile still needs to opt into its own reviewed WPS install and Provider enable step. Source-level verification does not imply that the installed profile was configured.

## WinGet

The official Windows App Installer already supplies `WindowsPackageManagerMCPServer.exe` under the per-user WindowsApps package-family alias `Microsoft.DesktopAppInstaller_8wekyb3d8bbwe`. Desktop formerly pointed to a missing `components/system-components/winget` copy, causing `configured=false` despite the OS component existing.

`desktop_runtime.system_components.official_winget_mcp` now resolves only the known App Installer package-family path. The Desktop staging layer uses that absolute executable when available and retains an explicit missing-component placeholder otherwise. It does **not** download arbitrary binaries, bypass per-tool privileges, enable a component, or change user-installed Windows packages.

A real, isolated Desktop Runtime successfully started the discovered WinGet MCP, found its two tools, and called `find-winget-packages` for `Git`. The returned list included `Git.Git`, confirming a functioning read-only search. The `install-winget-package` tool was never invoked. Report: `.desktop-build/rc8-winget-system-e2e.json`.

## Boundaries and next installation gate

The native RC.7 binary and its current private configuration are unchanged. The WinGet staging fix exists in source and requires a new frozen Runtime/NSIS build to ship. WPS was installed only into a temp test profile, not into the user's live profile. The remaining installed-profile steps are (1) reviewed WPS install and explicit enable, (2) rebuild for official WinGet discovery, and (3) native GUI/Tunnel acceptance of all 11 configured components. No push or public release was made in this phase.
