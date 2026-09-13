# Changelog

All notable PLA release changes are recorded here.

## 1.2.0 - 2026-09-13

### Added

- Global append-only Event Plane with bounded metadata/hashes, correlation and
  causation ids, cursor queries, and transaction-id correlation.
- Fail-open Observer Hook runtime with durable Hook invocation records and a
  built-in Audit Observer.
- Fail-closed Gate Hook runtime with deterministic deny-overrides policy and
  durable decision records.
- Manifest-backed External Observer plugins with isolated Python runtimes,
  bounded JSON stdio, hot-plug lifecycle controls, and no Capability-trigger API.
- Runtime Lifecycle Plane with an independent broker, durable restart requests,
  exact-current-HTTP PID verification, and stable
  `runtime.lifecycle_status/restart_http/restart_status` capabilities.
- v1.2 threat model and release-readiness documentation.

### Changed

- EventStore can filter by `transaction_id`; transaction-bound target
  Capability events now retain the real transaction id supplied internally by
  the Transaction Envelope.
- `start_all.ps1` and `stop_all.ps1` now manage the Runtime Lifecycle Broker
  in addition to HTTP, Tunnel, and the Interactive Elevation Broker.
- `restart_pla.ps1` accepts an exact expected HTTP PID and structured JSON mode
  for the Lifecycle Broker.
- Runtime governance remains behind the stable
  `capability_search / capability_describe / capability_invoke` surface.

### Verified

- Provider Doctor live probe: **6/6 healthy**, no detected pinned-version drift.
- Phase 3 Gate HTTP/Tunnel E2E passed.
- Phase 4 External Observer enable/disable/reload/remove hot-plug E2E passed
  without HTTP restart.
- Phase 5 native Runtime Lifecycle self-restart E2E passed while the Secure MCP
  Tunnel remained online.
- Consolidation Transaction/Event persistence E2E retained a committed
  transaction and transaction-correlated Event facts across an HTTP restart.
- Full cold stop/start E2E restored Elevation Broker, Lifecycle Broker, HTTP, and
  Secure MCP Tunnel, followed by a healthy 6/6 Provider Doctor live probe.
- Final v1.2.0 regression: **474 passed in 105.21 s**; main and all five isolated
  Python provider environments pass `pip check`.

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
- Stable `core.git_tag` / `core.git_push` Capability Broker gateways for release actions when the ChatGPT host has cached an older top-level MCP schema.

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