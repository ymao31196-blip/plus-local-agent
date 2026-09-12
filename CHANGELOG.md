# Changelog

All notable PLA release changes are recorded here.

## 1.1.0 - 2026-09-12

### Added

- Built-in `core.transaction_*` capability gateway over the durable Action
  Transaction Runtime.
- Transaction-gated capability policy with argument/result audit hashes and
  restart recovery.
- Interactive Elevation Broker for user-session Windows UAC mediation.
- Reviewed Software Migration provider with assessment, snapshot planning,
  registered uninstall, elevated WinGet installation, status, verification,
  and rollback-oriented orchestration.
- Reviewed Microsoft WinGet and Windows Management providers.
- Generic provider runtime support for isolated Python stdio and reviewed native
  executable stdio providers.
- Provider hot-plug control plane through `runtime.provider_status/rescan/reload/enable/disable`,
  allowing validated manifest-backed providers to be added, refreshed, hidden,
  restored, or removed without restarting PLA HTTP.
- Concurrent external provider discovery.
- Structured Git staging support for explicit non-ignored new regular files.
- Controlled release Git operations: atomic lightweight `git_tag` and confirmation-gated, non-force, atomic `git_push` with remote ref verification.

### Changed

- High-risk provider actions can require both explicit confirmation and an
  active transaction.
- New governance capabilities are available through the stable
  `capability_search / capability_describe / capability_invoke` surface,
  reducing dependence on ChatGPT host schema refreshes.
- `start_all.ps1` / `stop_all.ps1` manage the Interactive Elevation Broker
  alongside HTTP and the Secure MCP Tunnel.
- Architecture and provider documentation now describe the current capability,
  transaction, elevation, and migration planes.

### Verified

- Real Zotero C-drive to `D:\Apps\Zotero` migration completed and committed
  with install-location and user-data integrity verification.
- Provider Doctor reports all six reviewed providers healthy with no pinned
  Python dependency drift.
- Live hot-plug E2E added a temporary read-only WinGet-backed provider, disabled
  and re-enabled it, reloaded a changed manifest, removed the manifest, and
  rescanned the provider away while the PLA HTTP PID remained unchanged.

## 1.0.0 - 2026-09-11

- First frozen v1 release of the ChatGPT-native local capability runtime.
- Stable capability broker, Artifact Plane, reviewed document providers, durable
  task runtime, controlled local execution, and Secure MCP Tunnel integration.