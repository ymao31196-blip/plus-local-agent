# PLA Desktop RC.6 — isolated frozen-runtime optional-provider acceptance

Date: 2026-10-10
Source commit at build: `69e1b7e00a732c816d6f08afe14d64d0c6c88339`
Build installer: `dist/desktop-v1/PLA Desktop_1.0.0-rc.6_x64-setup.exe`
Build SHA-256: `a38322ecb55ef25ccb874829c7c54fd674b4b70ebf8be824bb6793a5e35278a4`

## Actual execution

The real frozen runtime payload from `desktop/src-tauri/resources` was used with independent temporary private data, no Tunnel credentials and no default Desktop configuration.

- Skill Library: `python desktop/verification/components_mcp.py --resources desktop/src-tauri/resources --skill-package workspace/skill-library --report .desktop-build/rc6-components-frozen-report.json --frozen`
- Exit: 0; 8/8 groups PASS. Actual v0.5.0 independent dependency installation and MCP service discovery (17 descriptors); source registration, sync, full SKILL.md/resource read, validation, activation/toggle, drafts, rename/archive/restore, bounded Unicode and local publication copy. Evidence: `.desktop-build/rc6-components-frozen-report.json`.
- WPS Office: `python desktop/verification/rc6_wps_isolated.py --resources desktop/src-tauri/resources --report .desktop-build/rc6-wps-isolated-report.json`
- Exit: 0; 9/9 checks PASS. Verified missing workdir blocks enable, actual reviewed pinned Git/npm installation, installation receipt and correct source directory, runtime lifecycle `ready`, 250 tools actually discovered. Evidence: `.desktop-build/rc6-wps-isolated-report.json`.

Both tests used separate disposable directories beneath the Windows user Temp folder; test fixture files only. The existing native Desktop process PID 34092 remained active before and after both executions, with no default-user-profile install, credential import, or Tunnel changes. No actual WPS document operations were performed.

## Native GUI acceptance remains open

A running RC.4 installation is currently registered under the same Windows product / Tauri single-instance identity as RC.6. The existing `desktop/verification/installation.ps1` refuses to install in this state (to prevent replacement), and installing RC.6 into a different folder under the same account would still collide in the installer registration and native single-instance routing.

Therefore **NSIS installed-GUI click-through is NOT TESTED** against RC.6 yet. It must run in a disposable Windows user/VM, or after a separately authorized transition away from the existing RC.4 installation. That final run should exercise the actual window: MCP install preview → confirmation → task progress/log → separate enable → connected tools; Skill Library install → enable → authorize root → add/sync local source → full Skill read, including failure/retry/restart. Confirm the installed execution path and hashes and inspect the actual artifact payload. Do not claim clean-Windows, signed or remote ChatGPT E2E.
