# PLA Desktop 1.0.0-rc.1 — unsigned local test candidate

The candidate provides a current-user Windows NSIS installer, a Tauri 2 management window and tray, first-run configuration, Windows DPAPI credential storage, named workspace permissions, actual Runtime/Tunnel/browser detection, controlled start/stop/restart, redacted logs, diagnostics and an opt-in login-start preference.

The complete PyInstaller onedir Runtime preserves existing MCP wrappers, capability routing and source/headless support. A standalone Python execution component, fixed Node/Playwright browser component and official narrow Secure MCP Tunnel runtime are bundled. No ChatGPT replacement or local model engine is added.

Automatic verification passed 825 source tests, actual frozen/installed MCP file/process/transaction/browser calls, installed UI and process-ownership lifecycle, negative security/credential checks, and real NSIS installation/uninstall with user-file retention. See the acceptance report for exact grades and evidence.

**The release is not yet fully qualified.** Real positive Tunnel traversal, real ChatGPT authorization/calls, clean Windows 10/11, OS reboot and manual tray-menu acceptance remain open. Office/Skills external environments are missing and explicitly unavailable. Current dependency versions are frozen in the build inputs; they are not silently updated at runtime.

This package is unsigned. Windows Authenticode and Tauri updater signing are separate concerns; neither is configured. Automatic updating is disabled. Manual upgrade requires verifying source and SHA-256, exiting the app, and running the reviewed installer while retaining user data. No public push, Release or public hosting is authorized or performed.

Installer: `PLA Desktop_1.0.0-rc.1_x64-setup.exe` (89,738,878 bytes).

SHA-256: `854392e35310134d5e079961435e81630ca391176b615df9d11668234139b437`.

Build source: `e711724528a05cf459ea6f2d0903ac0633fddcf2` on `codex/pla-desktop-v1`, clean at build. Later documentation-only commits do not change the packaged code snapshot.
