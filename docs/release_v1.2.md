# PLA v1.2.0 Freeze Checklist

## Release scope

v1.2.0 is a backward-compatible governance and runtime-observability release on
top of the v1.1.0 provider/elevation baseline.

The production decision boundary does not change:

- ChatGPT remains the sole production planner and capability selector.
- PLA remains the local execution, policy, state, audit, and recovery runtime.
- The stable ChatGPT-facing surface remains
  `capability_search → capability_describe → capability_invoke`.
- MCP Sampling remains experimental/non-production and is not required.
- Existing reviewed providers and v1.1 transaction/elevation boundaries remain
  backward compatible.

v1.2.0 consolidates five phases:

1. Event Plane.
2. Observer Hooks.
3. Gate Hooks.
4. External Observer Plugins.
5. Runtime Lifecycle Plane.

## Required before release commit/tag

- [x] Runtime/version set to `1.2.0`.
- [x] Git started this consolidation from clean HEAD
      `b736881254a42e753e5bf91a696248233e88dca7`.
- [x] Provider Doctor live probe reports **6/6 healthy**, **0 degraded**,
      **0 disabled**, with no detected pinned Python-provider version drift.
- [x] Main environment and all five isolated Python provider environments pass
      `pip check` with no broken requirements.
- [x] Capability Registry currently exposes **65 capabilities**:
      **12 core**, **13 runtime**, and **40 reviewed provider capabilities**.
- [x] Runtime capability set includes:
      **5 provider hot-plug**, **5 external Observer**, and
      **3 Lifecycle** capabilities.
- [x] Core capability set includes:
      **5 transaction**, **2 controlled Git release**,
      **1 Event query**, **2 Observer inspection**, and
      **2 Gate inspection** capabilities.
- [x] Event Plane is append-only, cursor-queryable, and stores bounded hashes /
      metadata instead of raw Capability arguments or results.
- [x] Built-in Audit Observer is fail-open and sees already-persisted Event facts.
- [x] Gate runtime uses deterministic deny-overrides and fails closed on Gate
      execution, validation, or decision-persistence failures.
- [x] No default production Gate is registered, preserving existing behavior.
- [x] External Observer plugins are isolated-process, manifest-backed, hot-pluggable,
      fail-open, and cannot trigger PLA Capabilities through the Observer API.
- [x] Runtime Lifecycle Broker performs exact current-HTTP restart only; it does
      not expose arbitrary process management.
- [x] Threat model documented in `docs/v1_2_threat_model.md`.
- [x] Real Phase 3 Gate HTTP/Tunnel E2E passed.
- [x] Real Phase 4 external Observer hot-plug E2E passed without HTTP restart.
- [x] Real Phase 5 native self-restart E2E passed with unchanged Tunnel PID.
- [x] Consolidation found and fixed a cross-plane audit gap: transaction-bound
      target Capability events now retain the real `transaction_id`, and
      `core.event_query` can filter by transaction id.
- [x] Post-fix Transaction/Event E2E:
      Transaction `33d31e39cd5c4eb78a3fc9fe660bb486` committed at revision 4;
      Event sequences **68/69** carry that exact transaction id.
- [x] Post-fix restart-persistence E2E:
      HTTP **22640 → 22600**, Tunnel PID **884** unchanged; after restart, the
      committed Transaction and Event 68/69 remained queryable and Audit Hook
      cursor continued past 72.
- [x] Final v1.2.0 full `pytest -q` regression passes after consolidation fixes:
      **474 passed in 105.21 s**.
- [x] Final project/test Python compilation audit passes:
      **103 Python files**, **0 errors**.
- [x] Full cold stop/start E2E passed after the release changes:
      Tunnel **884 → 28036**, Lifecycle Broker **25884 → 6632**,
      HTTP **22600 → 19988**, and Elevation Broker **19536 → 24900**.
      All four old processes stopped cleanly, all four new components became
      ready, and Provider Doctor again reported **6/6 healthy**.
- [x] Final Git status/diff contains only the **14 intended** v1.2 consolidation/release files; no runtime state, credentials, temporary E2E manifests, or local environments are tracked.
- [ ] Release tag created only after the release commit.
- [ ] Push performed only through the controlled Git boundary after explicit
      release authorization.

## Security boundary

v1.2.0 does not make PLA an OS sandbox.

Important preserved assumptions:

- child providers and external Observers run with host-user authority;
- same-user host compromise is outside PLA's protection boundary;
- remote ingress depends on the reviewed Secure MCP Tunnel deployment;
- Gate infrastructure is available but no default policy Gate is active;
- Event/Observer persistence is observability and intentionally fail-open;
- Gate failure is intentionally fail-closed;
- external Gate plugins and plugin-triggered Capability execution are not
  supported;
- Interactive Elevation Broker remains the only reviewed UAC mediation path;
- Lifecycle Broker cannot select arbitrary processes or commands.

See `docs/v1_2_threat_model.md` for the full model.

## Compatibility notes

- Existing v1.1 provider manifests remain valid.
- Existing stable Capability Broker entrypoints are unchanged.
- Existing Action Transaction Store remains the transaction source of truth.
- Existing Task Store remains the task source of truth.
- EventStore adds transaction-id correlation/filtering without changing Event
  schema version 1.
- External Observer and Lifecycle controls are additive runtime capabilities.
- `start_all.ps1` now ensures both Elevation and Lifecycle brokers.

## Current MCP alignment note

The 2026-07-28 MCP specification moves protocol concerns toward a stateless core,
formal extensions, and hardened authorization. PLA's Event/Observer/Gate/Lifecycle
planes remain implementation-level local runtime governance rather than invented
MCP protocol methods. Production PLA also does not depend on deprecated Sampling
for its agent brain.

## Freeze rule

After the v1.2.0 release commit, further changes should be classified as:

- patch: bug, security, reliability, or documentation fixes that preserve public
  contracts;
- minor: backward-compatible reviewed capabilities or governance planes;
- major: incompatible stable Capability Broker contracts, Artifact identity
  changes, or material security-boundary changes.

Do not add capabilities solely to increase feature count.
