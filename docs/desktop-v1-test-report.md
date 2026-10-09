# PLA Desktop V1 acceptance report — 2026-10-09

This report distinguishes implemented behavior, executed evidence and the owner's still-open final acceptance criteria.

## Result

**Unsigned local test candidate built and automatically verified; full release qualification is BLOCKED.** Positive Secure MCP Tunnel traversal, real ChatGPT authorization/calls, a clean Windows machine and OS restart evidence are still required. An automatic MCP client is not treated as ChatGPT acceptance. No public push, Release or download link was created.

| Artifact | Value |
| --- | --- |
| Desktop version | `1.0.0-rc.1` |
| Runtime protocol implementation | Existing PLA `2.0.1`, FastMCP `4.0.3`, MCP `2.2.0` |
| Build source revision | `e711724528a05cf459ea6f2d0903ac0633fddcf2` |
| Branch | `codex/pla-desktop-v1` |
| Build source dirty | `false` |
| Installer | `D:\AI_Tools\plus-local-agent\dist\desktop-v1\PLA Desktop_1.0.0-rc.1_x64-setup.exe` |
| Installer bytes | `89738878` |
| SHA-256 | `854392e35310134d5e079961435e81630ca391176b615df9d11668234139b437` |
| Authenticode | `NotSigned` — actually checked |
| Automatic update | Disabled; no updater plugin or unsigned download/execute path |
| Actual installation test host | Windows kernel build `10.0.26200.0`, existing developer machine |

## Executed results

| Area | Status | Actual evidence |
| --- | --- | --- |
| Full source regression | PASS | `825 passed in 136.40s`; `.desktop-build/final-source-regression.log` |
| Complete frozen onedir construction | PASS | `runtime-build.log`; complete DLL/module/metadata directory retained, not an executable-only sidecar |
| Reproducible local build script | PASS | Multiple executed full builds using pinned Python/npm/Cargo inputs and verified public archives; final `qualified-local-build.log` |
| NSIS executable installer | PASS | Actual `makensis` output and installed application, not development mode |
| Packaged MCP discovery | PASS | 23 expected public tool wrappers discovered from frozen Runtime |
| Local read/create/modify | PASS | Real MCP calls on frozen payload and installed payload; explicitly authorized external root also exercised |
| Controlled process | PASS | Bundled Python process actually executed; separate named-pipe runner also executed from an authorized root |
| Complex existing capability | PASS | Real hash-checked two-file transactional changeset; real browser Provider navigation and semantic inspection |
| Local negative/security paths | PASS | Traversal, unapproved `cmd.exe`, private paths, application-resource mutation and confirmed private-data workspace registration rejected |
| Management boundary | PASS | Frozen stdio interface refused generic Shell and executable override |
| Port conflict | PASS | Existing foreign test listener retained; no child adopted or killed |
| Runtime crash/recovery | PASS | Fresh owned Runtime killed by harness, observed as exited, then restarted successfully |
| Wrong runtime API key | PASS | Synthetic invalid key produced explicit real remote authentication rejection and was never reported connected |
| Tunnel unexpected exit | PASS | Owned Tunnel terminated by harness; exit was observed |
| DPAPI/log redaction | PASS | Stored ciphertext contained no plaintext synthetic key; log output and persisted log did not contain it |
| First installed wizard and UTF-8 paths | PASS | Actual installed Tauri/WebView2 UI driven, including Chinese private-directory paths |
| GUI workspace authorization | PASS | Real UI input persisted directory and read/write/execute choices |
| GUI -> native IPC -> Runtime -> MCP tool | PASS | Real diagnostic tool call triggered from installed frontend |
| Login-start preference | PASS | Actual HKCU opt-in entry and opt-out removal; defaults off |
| Single instance | PASS | Second native process exited while original instance remained active |
| Close-to-tray behavior | PASS | Native close event hid the window while actual Runtime remained MCP-ready; window could be shown again |
| Normal exit/reopen | PASS | Installed app exited, reopened, retained port and authorized-directory configuration |
| Native abnormal exit ownership | PASS | Killing the owned native test process reaped Runtime, runner, browser supervisor, Playwright MCP and keeper through the Windows Job |
| Frontend runtime errors | PASS | No JavaScript page errors during installed UI test |
| Actual uninstall | PASS | NSIS uninstaller removed native application; exit code 0 |
| User data retention | PASS | Default private-data marker and MCP-created file in explicitly authorized external workspace survived uninstall |
| Existing developer services | PASS | Loopback 8766 PID 30744, 8931 PID 19464 and 18081 PID 20620 were identical before and after installation/uninstall |

The final frozen MCP suite contains **10 PASS** checks. The installed native UI suite contains **10 PASS** checks. The installation/uninstall wrapper contains **7 PASS** checks. The fault suite contains **7 PASS** checks and one **NOT TESTED** dedicated-network interruption check. The source suite contains **825 PASS** tests.

## Open final acceptance

| Required acceptance | Status | Blocking condition or missing evidence |
| --- | --- | --- |
| Positive remote MCP client -> Secure MCP Tunnel -> packaged Runtime -> tool -> response | BLOCKED | Needs a separate legitimate test Tunnel ID and runtime key. Existing developer credentials were neither imported nor reused. |
| Real ChatGPT account authorization | BLOCKED | Personal/account-side action; local app cannot automatically complete or assert it. |
| Real ChatGPT tool calls through Tunnel | BLOCKED | Must be performed after real account authorization; preserve conversation evidence separately. |
| Clean Windows 10 x64 | BLOCKED | No clean VM/test machine supplied; no Windows Sandbox executable or configured VM runtime found. |
| Clean Windows 11 x64 | BLOCKED | Current host is a developer machine; isolated installation does not establish clean-machine compatibility. |
| System restart and actual login-start after reboot | BLOCKED | Requires a safely rebootable independent environment; this working host was not rebooted. |
| Live authenticated Tunnel network interruption/recovery | NOT TESTED | Requires positive authenticated connection and a dedicated test network. |
| Every physical system-tray menu item click | NOT TESTED | Native tray creation/close lifecycle and underlying operations are implemented; physical click-through remains a manual acceptance item. |
| Frozen mandatory component missing | PASS | Actual frozen manager invoked with an empty isolated resource directory; startup explicitly refused before borrowing developer components. |
| Full corrupted-native-install matrix on clean machine | NOT TESTED | Payload presence and frozen prerequisite refusal checked; clean-machine native repair scenarios still need independent environment. |
| Office/Skills desktop execution | NOT TESTED | Independent external provider environments are not bundled; UI explicitly reports missing components. No readiness or successful calls claimed. |
| Trusted signed public distribution | BLOCKED | No Windows certificate, updater signing/release infrastructure or public distribution approval supplied. |
| Hosted CI execution | NOT TESTED | Workflow added but no public push or remote workflow dispatch performed. |

Therefore **not all P0 acceptance items have passed**. This candidate is not declared the completed trustworthy public Desktop V1 release.

## Important fixes verified during development

- Source PowerShell 5.1 launches now discard the case-insensitive inherited PowerShell 7 module path. Three previously failing host module tests passed without loosening `ExecutionPolicy Restricted` or cmdlet policy.
- Desktop state is separate from application resources. Frozen subprocess roles explicitly launch existing runner/keeper/indicator code. The independent runner is started using the existing supervisor from desktop Runtime lifespan.
- Desktop resource root is read-only/non-executable through file/process root policy; the existing workspace validator also rejects private desktop data through confirmed MCP registration.
- `/readyz` alone was found to return success before remote authentication. Connection state now requires a fresh real successful poll metric, rejects newly observed errors and expires stale success. Synthetic wrong-key regression passed on the final payload.
- Tunnel now uses checksum-verified official `v0.0.16` narrow runtime-cloudflared distribution. Its OS/network environment is allowlisted so developer profile/config and raw HTTP log flags cannot be inherited.
- Desktop browser uses the bundled fixed Node component and cannot silently fall back to a developer's Node installation when that component is missing.
- Native login-start changes preserve a different existing registration and roll back on config persistence failure.
- Main-window close/show/visibility permissions were added only for the local `main` window after the first installed close test correctly reported an ACL failure. Final installed tests passed.

## Scope and practical limits

The original source/headless startup remains available. Python, Rust and Node build tools are not end-user installation requirements. WebView2 is managed by the embedded official bootstrapper; a machine without it needs network access to Microsoft. Tool execution runs with the existing current-user semantics; root/CWD and program policy are not an OS sandbox. The standalone execution interpreter includes the standard library, not pip/pytest or arbitrary third-party libraries. Git, TeX, WSL and independent Office/Skills environments remain optional external dependencies and are not claimed installed.

Dependencies and all preserved license texts are in installed `resources/licenses`, `resources/python/LICENSE.txt`, and the Tunnel archive's LICENSE/NOTICE/SPDX/license report. The local delivery directory includes a copied license tree and concise notice inventory. Public first-party licensing/distribution decisions remain with the owner.

## Evidence and continuation

Final local delivery evidence is copied to `dist/desktop-v1/evidence/`: regression/build logs, packaged MCP/fault reports, installed MCP/native UI/installation reports and a real installed-window screenshot. Test directories and user data were preserved as evidence; no unknown user data were deleted.

Use `docs/desktop-v1-acceptance.md` to complete real Tunnel, ChatGPT and clean-machine acceptance. Enter a separate test key in the GUI password field, not in chat or shell history. Follow the account's current official authorization flow. Record actual successful tool responses before marking the open rows PASS.
