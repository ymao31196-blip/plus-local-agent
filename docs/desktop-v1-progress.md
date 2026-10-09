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

1. Audit and baseline tests — IN PROGRESS.
2. Runtime packaging/path and process adaptations — PENDING.
3. Minimal native management boundary, configuration wizard and desktop controls — PENDING.
4. Frozen onedir runtime, NSIS and deterministic dependency inputs — PENDING.
5. Packaged MCP, lifecycle, negative/security and isolated installation checks — PENDING.
6. Bilingual instructions, license inventory, release/hash and final acceptance handbook — PENDING.

## Evidence rules

PASS means actually executed successfully. FAIL means executed unsuccessfully. BLOCKED requires a real unavailable dependency/action. NOT TESTED means no execution evidence. Local MCP does not establish Tunnel traversal, account authorization or ChatGPT acceptance. Developer-machine installation is not a clean Windows environment. No public push/release is authorized. Automatic updates stay disabled without signing infrastructure.

## Sources checked

- https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- https://github.com/openai/tunnel-client
- https://raw.githubusercontent.com/openai/tunnel-client/main/LICENSE
- https://v2.tauri.app/distribute/windows-installer/
- https://v2.tauri.app/start/prerequisites/
