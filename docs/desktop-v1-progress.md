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

## Evidence rules

PASS means actually executed successfully. FAIL means executed unsuccessfully. BLOCKED requires a real unavailable dependency/action. NOT TESTED means no execution evidence. Local MCP does not establish Tunnel traversal, account authorization or ChatGPT acceptance. Developer-machine installation is not a clean Windows environment. No public push/release is authorized. Automatic updates stay disabled without signing infrastructure.

## Sources checked

- https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- https://github.com/openai/tunnel-client
- https://raw.githubusercontent.com/openai/tunnel-client/main/LICENSE
- https://v2.tauri.app/distribute/windows-installer/
- https://v2.tauri.app/start/prerequisites/
