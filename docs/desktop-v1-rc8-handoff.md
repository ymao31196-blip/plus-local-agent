# PLA Desktop 1.0.0-rc.8 — candidate source acceptance and native build handoff

Date: 2026-10-10. Branch: `codex/pla-desktop-v1`. This note distinguishes **source Runtime acceptance** from the **Windows installer release gate**.

## Source candidate contents

- Version unified to `1.0.0-rc.8` in Desktop Python config, npm package/lock, Tauri configuration, Cargo package/lock and the version assertion test.
- ChatGPT-native Skill Library can preview and submit a reviewed local `chatgpt-skill-library==0.5.0` source/wheel using `skill_package` and a manifest/source-content-bound SHA-256. Installation, enablement and Skill source sync remain separately controlled. The original user repository is not modified.
- Official Windows App Installer WinGet MCP is discovered from the known per-user Microsoft package-family alias. A missing OS component remains visibly unconfigured and is not replaced with an arbitrary downloaded executable. Existing generated RC.7 manifest pointers migrate only while they remain unchanged from tracked bundled assets; manual user modifications are preserved.
- WPS Office remains an **opt-in**, pinned upstream Git/npm installation; a present Node binary is not evidence that its separate source and dependencies have been installed.
- Reviewed starter pack is opt-in for seven providers. Third-party MCP installs and privileged tools are not automatically enabled.

## Executed tests, independently verifiable

- `desktop/verification/rc8_all_providers_e2e.py`: One isolated temporary Desktop private data environment containing the seven real previously installed starter-pack environments, plus a newly installed Skill Library v0.5.0 and pinned WPS source, using the packaged RC.7 resource payload and RC.8 **source** manager. All **11/11** providers reached `ready` with **349 actual discovered tools**, preserving the Runtime PID. It explicitly stops all owned test processes. `.desktop-build/rc8-all-11-providers-e2e.json`.
- Isolated WPS source installation and read-only `wps_common_ping` / `wps_check_connection` responded `WPS连接正常！pong` and `connected=true`. `.desktop-build/rc7-wps-isolated-real.json`; `.desktop-build/rc8-wps-readonly-e2e.json`.
- Official WinGet MCP initialized, enumerated its two tools and successfully executed read-only package search. `.desktop-build/rc8-winget-system-e2e.json`.
- `tests/test_desktop_system_winget.py` covers strict package-family discovery, missing fallback, no implicit enablement, RC.7-generated manifest path migration and protection of user-edited manifests.
- Source Python suite and selected Desktop tests should be rerun after the final version bump before approval. These tests cannot substitute for the compiled binary.

## Developer-host Windows build gate

The current PLA local execution policy explicitly rejects `node` and `powershell.exe` as run_process programs. The assistant **must not bypass that policy** using arbitrary Python subprocesses, GUI automation, or another MCP node. The original trusted developer can run these commands in their own terminal:

```powershell
Set-Location "D:\AI_Tools\plus-local-agent"
node .\desktop\verification\components-ux-smoke.cjs
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\desktop\packaging\build.ps1"
```

The second command performs a full fresh PyInstaller Runtime build and packaged MCP checks, followed by Rust/Tauri/NSIS, license collection, SHA-256 and source-dirty manifest recording. Do **not** pass `-SkipRuntime` or `-SkipAcceptance` for an RC.8 release candidate. It should produce:

`dist/desktop-v1/PLA Desktop_1.0.0-rc.8_x64-setup.exe`

Check the precise file and `build-manifest.json` before running any native tests. A missing file is **not a build success**.

## Native and user-data protection gate

The current Windows user already has a live RC.7 at `D:\PLA Desktop`. Do not install RC.8 over it without a deliberate upgrade test and full snapshot/rollback. Existing `desktop/verification/installation.ps1` intentionally refuses to run when a PLA Desktop installation is registered. Use an independent clean Windows user/VM for an actual isolated NSIS installation, uninstall and migration test. `desktop/verification/native-ui.cjs` can check an independently launched native executable/WebView2 UI using its own `--data-dir` only after a corresponding RC.8 executable has been built. It must not be pointed at an in-use application.

The release candidate remains **unreleased** until independent native WebView2 GUI, installed frozen Runtime, actual ChatGPT Tunnel capability discovery, Skill search/read, WPS read-only ping, official WinGet search, failure/retry behavior, rollback and existing user config preservation have passed. A source-only 11/11 result does not imply installed-app 11/11.

## 2026-10-10 actual RC.8 build / UI smoke discrepancy

The developer successfully ran the full Windows build and generated `PLA Desktop_1.0.0-rc.8_x64-setup.exe`. The independently checked artifact is 92,779,665 bytes and has SHA-256 `1200aa88823c8ce6c6b7c4a5e7bb64cb24d8da1781ec5ac55afe50bc9abd3b28`; `build-manifest.json` records source commit `01a48be1d3019db5637e70634e22fdfdbfac5cb8`, `source_dirty=false`, `signed=false`, and disabled updates.

The separately executed `node desktop/verification/components-ux-smoke.cjs` **failed** on an obsolete assertion expecting the transient message `安装任务已启动`. The UI had already refreshed job state to `docx 正在安装（PID 123）…`; its preview SHA, confirmed start, and live job were exercised successfully before that assertion. The script now asserts the running job instead of transient wording, and `build.ps1` runs this smoke as a **blocking early preflight** (unless acceptance was explicitly skipped), logging to `.desktop-build/components-ux-smoke.log`. Subsequent Python contract tests passed; the Node smoke still needs to be rerun in the trusted Windows terminal before declaring UI smoke PASS.

The existing RC.8 installer is a valid **built artifact, not an accepted release candidate**. These later changes affect only test/build orchestration, not the packaged app source; avoid replacing or installing over the active Desktop. A new official delivery should use a distinct version/commit and its own hash after native WebView2/Tunnel checks.
