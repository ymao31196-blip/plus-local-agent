# PLA Desktop V1 implementation and evidence

This file records the Desktop V1 implementation against the owner's unchanged acceptance criteria.

## Baseline — 2026-10-09

- Source baseline: `846a595`, clean `master`; implementation branch: `codex/pla-desktop-v1`.
- Runtime: Python 3.11.16 in the existing developer Conda environment; FastMCP 4.0.3; MCP 2.2.0; server version 2.0.1.
- Source startup: `start_all.ps1` starts elevation and lifecycle brokers, execution runner, Playwright MCP and keeper, HTTP Runtime, then Tunnel.
- Existing developer services occupy loopback 8766, 8931 and 18081. They must remain untouched. Desktop uses separately configured free ports and isolated user data.
- Workspaces already support named roots with read/write/execute permissions and config hashes. The desktop must reuse this validator.
- Packaging gaps: paths based on `__file__`, subprocess launches using Python source scripts, provider-specific Python/Node environments, and detached runner lifetime.
- Existing Python interpreter, Node and developer Tunnel are evidence only, never a customer prerequisite or credential import.
- Tauri/Rust/MSVC/NSIS are absent from PATH. Evaluating a local GNU Rust toolchain before deciding feasibility.
- Official Tunnel source declares Apache-2.0; release archives publish license/SBOM sidecars. Preserve LICENSE, NOTICE and dependency license evidence; bundle only checksum-verified public release binaries, never the existing secrets directory.

## Stages

1. Audit and baseline — DONE. Three host PowerShell module-path failures were fixed without changing execution policy; full regression subsequently passed 821 tests.
2. Runtime packaging/path and process adaptations — IMPLEMENTED. Frozen files/processes, independent named-pipe runner, transactional changeset and real browser Provider calls passed. Final private-data/root-policy changes are passing source security checks and await the next frozen build.
3. Native management, wizard and desktop controls — IN VERIFICATION. Installed UI passed wizard, UTF-8 directories, workspace configuration, real diagnostic and single-instance checks. Close-to-tray test initially hit an ACL denial; necessary main-window permissions have been added and await repeat verification. No failed item is marked PASS.
4. Frozen Runtime and NSIS — FIRST COMPLETE SCRIPT BUILD PASSED. The intermediate package was installed and uninstalled; WebView2Loader.dll, verified Python/Node/Tunnel assets and license texts were included. It will be superseded by a clean-revision build after final security and UI fixes.
5. Full acceptance — IN PROGRESS. Isolated developer-host installation/uninstall and user-data retention passed; source listeners on 8766/8931/18081 retained their exact original PIDs. Native quit/reopen/crash tests await completion. Real Tunnel and ChatGPT acceptance, clean-machine and OS-reboot checks remain open.
6. Documentation and release evidence — DRAFTED. Bilingual README, architecture, handbook, dependency inventories and build/CI inputs exist; final report/hash remain pending final package verification.

## Current security changes

- Desktop `pla` resource root is read-only and non-executable through user-facing root policy; source/headless defaults are unchanged.
- The existing Runtime workspace validator also rejects overlap with private desktop data. This closes an alternative path through confirmed MCP workspace registration, not just the GUI.
- HKCU login-start changes preserve a foreign existing PLA Desktop registration and restore the previous value if config persistence fails.
- Existing-source detection is metadata-only and never imports credentials or adopts processes.

## External acceptance prerequisites

- A separate legitimate Tunnel ID/runtime key and account-side authorization are required for positive remote E2E. Existing developer credentials have not been imported or replaced.
- No Windows Sandbox executable or configured VM runtime was found on this host. A clean Windows test environment and reboot validation need an external test machine/VM.
- Windows signing certificate and trusted update-signing/release infrastructure were not supplied; only an unsigned local test candidate is authorized.

## Final local checkpoint — 2026-10-09

All executed final automatic suites passed: 825 source tests; 10 frozen/installed MCP checks including an explicitly authorized external directory, independent runner, transaction and real browser calls; 10 installed UI/lifecycle checks; 7 installation/uninstall checks; 7 packaged fault checks. The dedicated authenticated-network interruption remains NOT TESTED. The frozen manager also refused a missing mandatory-component payload. The final installer was installed and uninstalled, user data and the MCP-created authorized workspace file survived, and the exact three original developer listener PIDs were unchanged.

Build source: `e711724528a05cf459ea6f2d0903ac0633fddcf2` (clean). Package: `dist/desktop-v1/PLA Desktop_1.0.0-rc.1_x64-setup.exe`, 89,738,878 bytes, SHA-256 `854392e35310134d5e079961435e81630ca391176b615df9d11668234139b437`, Authenticode `NotSigned`.

Stages 1–4 are complete for this local test candidate; automatic portions of stage 5 passed; stage 6 delivery documentation/evidence is assembled. Full acceptance remains BLOCKED by legitimate test Tunnel credentials, personal ChatGPT authorization and a clean, safely rebootable Windows environment. No overall P0/public-release qualification is claimed. See `desktop-v1-test-report.md`, `desktop-v1-release-notes.md` and the acceptance handbook for precise grades and continuation.

## Evidence rules

PASS means actually executed successfully. FAIL means executed unsuccessfully. BLOCKED requires a real unavailable dependency/action. NOT TESTED means no execution evidence. Local MCP does not establish Tunnel traversal, account authorization or ChatGPT acceptance. Developer-machine installation is not a clean Windows environment. No public push/release is authorized. Automatic updates stay disabled without signing infrastructure.

## Sources checked

- https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- https://github.com/openai/tunnel-client
- https://raw.githubusercontent.com/openai/tunnel-client/main/LICENSE
- https://v2.tauri.app/distribute/windows-installer/
- https://v2.tauri.app/start/prerequisites/
# 2026-10-09 用户安装后的真实连接复测

用户提供桌面 `test.txt` 中的独立测试 Tunnel ID / Runtime key，并授权操作已安装 GUI。Computer Use 实测发现已有 Runtime 已就绪，但 Tunnel ID 为空；填写时保存被运行状态拒绝，英文错误缺少配置页直接停止入口。停止本应用服务后，GUI 保存测试凭据成功（DPAPI），启动 Runtime / Tunnel 后远端元数据获取及成功控制面轮询通过，界面显示「已连接远端服务」。GUI 发起真实本地 MCP 诊断返回 PASS。测试过程中未读取或修改原开发连接凭据。

修复配置页服务控制入口及常见错误中文提示；修复完成向导时停止服务、丢失已验证状态并断开 Tunnel 的问题。新增原生 UI 回归断言：完成向导后 Runtime PID 不变，仍 ready，本地验证仍通过。RC.2 已实际更新用户安装，三份配置 SHA-256 保持一致；Computer Use 已验证草稿刷新保留及完成向导后双服务 PID 不变、本地验证仍通过。新打包 MCP / 浏览器 10 项与当前后端 10 项回归通过，实际安装后文件/进程/事务 4 项通过。2026-10-10 用户完成 PLA-TEST 连接；真实 ChatGPT 文件创建/读取/修改、独立 runner 的 Python 版本执行及 SHA 校验事务修改全部通过，本地最终内容/哈希与 ChatGPT 返回一致。干净 Windows 与系统重启仍受外部条件阻塞。详见 desktop-v1-rc2-followup.md。
