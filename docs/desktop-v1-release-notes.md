# PLA Desktop 1.0.0-rc.4 — unsigned local test candidate

RC.4 adds complete Provider management, optional independent component installation, a dedicated complete cached Skill List alongside the 17 Skills Management workflows, and opt-in source self-development. Frozen MCP, real Skill service, native GUI and configuration-preserving installed upgrade passed. See [RC.4 evidence and remaining qualification](desktop-v1-rc4-followup.md).

RC.3 separates adding a new root from explicitly editing an existing root. Duplicate additions are refused, saving clears the add form, and multiple roots retain their independent paths and permissions. Current full source tests (826), frozen multi-root/MCP checks, actual NSIS update and isolated installed Computer Use checks passed. See [the RC.3 follow-up](desktop-v1-rc3-followup.md).

RC.2 preserves unsaved connection drafts across refreshes, adds service controls to setup, translates common operation errors, and keeps Runtime/Tunnel running when completing the wizard. Actual user-installed upgrade, positive remote control-plane polling and GUI fix verification passed; real ChatGPT file/independent-runner/transaction calls also passed on 2026-10-10. See [the follow-up report](desktop-v1-rc2-followup.md).

The candidate provides a current-user Windows NSIS installer, a Tauri 2 management window and tray, first-run configuration, Windows DPAPI credential storage, named workspace permissions, actual Runtime/Tunnel/browser detection, controlled start/stop/restart, redacted logs, diagnostics and an opt-in login-start preference.

The complete PyInstaller onedir Runtime preserves existing MCP wrappers, capability routing and source/headless support. A standalone Python execution component, fixed Node/Playwright browser component and official narrow Secure MCP Tunnel runtime are bundled. No ChatGPT replacement or local model engine is added.

Automatic verification passed 825 source tests, actual frozen/installed MCP file/process/transaction/browser calls, installed UI and process-ownership lifecycle, negative security/credential checks, and real NSIS installation/uninstall with user-file retention. See the acceptance report for exact grades and evidence.

**The release is not yet fully qualified.** Real positive Tunnel traversal and ChatGPT authorization/file/process/transaction calls passed. Clean Windows 10/11, OS reboot and manual tray-menu acceptance remain open. Office/Skills are independent optional installations; missing dependencies remain explicitly unavailable. Skill Library redistribution permission is not established, so installation requires the user's matching source or wheel. RC.4 added-capability ChatGPT calls remain untested. Current dependency versions are frozen in the build inputs; they are not silently updated at runtime.

This package is unsigned. Windows Authenticode and Tauri updater signing are separate concerns; neither is configured. Automatic updating is disabled. Manual upgrade requires verifying source and SHA-256, exiting the app, and running the reviewed installer while retaining user data. No public push, Release or public hosting is authorized or performed.

Installer: `PLA Desktop_1.0.0-rc.4_x64-setup.exe` (92,547,687 bytes).

SHA-256: `28b4da3fc6baeb7bd38adeee8af01eb8e401c17e33f6a46dd0707e99743e7ae0`.

Build source: `578f9a0da9928c06b97c2d9269f39d2e79b99021`, source_dirty=false. Previous version packages and evidence are archived separately.
