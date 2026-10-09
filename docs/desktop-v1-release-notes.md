# PLA Desktop 1.0.0-rc.2 — unsigned local test candidate

RC.2 preserves unsaved connection drafts across refreshes, adds service controls to setup, translates common operation errors, and keeps Runtime/Tunnel running when completing the wizard. Actual user-installed upgrade, positive remote control-plane polling and GUI fix verification passed; full ChatGPT calls remain pending. See [the follow-up report](desktop-v1-rc2-followup.md).

The candidate provides a current-user Windows NSIS installer, a Tauri 2 management window and tray, first-run configuration, Windows DPAPI credential storage, named workspace permissions, actual Runtime/Tunnel/browser detection, controlled start/stop/restart, redacted logs, diagnostics and an opt-in login-start preference.

The complete PyInstaller onedir Runtime preserves existing MCP wrappers, capability routing and source/headless support. A standalone Python execution component, fixed Node/Playwright browser component and official narrow Secure MCP Tunnel runtime are bundled. No ChatGPT replacement or local model engine is added.

Automatic verification passed 825 source tests, actual frozen/installed MCP file/process/transaction/browser calls, installed UI and process-ownership lifecycle, negative security/credential checks, and real NSIS installation/uninstall with user-file retention. See the acceptance report for exact grades and evidence.

**The release is not yet fully qualified.** Real positive Tunnel traversal, real ChatGPT authorization/calls, clean Windows 10/11, OS reboot and manual tray-menu acceptance remain open. Office/Skills external environments are missing and explicitly unavailable. Current dependency versions are frozen in the build inputs; they are not silently updated at runtime.

This package is unsigned. Windows Authenticode and Tauri updater signing are separate concerns; neither is configured. Automatic updating is disabled. Manual upgrade requires verifying source and SHA-256, exiting the app, and running the reviewed installer while retaining user data. No public push, Release or public hosting is authorized or performed.

Installer: `PLA Desktop_1.0.0-rc.2_x64-setup.exe` (89,755,283 bytes).

SHA-256: `9db98b5a3ab4559e80dc5fceaf0313aa56dd4dbb19c969f4f376f8fa3f7db148`.

Build source: `685f38ef2071369af770c73548f959d1b4a56b17` on `codex/pla-desktop-v1`. The manifest records source_dirty=true from a Cargo.toml line-ending rewrite; no content diff remained. See the RC.2 follow-up for the exact build and test limits.
