# PLA v1.6.0

PLA v1.6.0 expands the local runtime from a read-oriented execution layer into a governed Windows action and resumable external-action runtime while keeping ChatGPT as the Agent Brain and PLA as the bounded local executor.

## Structured Windows diagnostics

The reviewed PowerShell surface now returns structured JSON observations instead of relying on free-form text for supported commands. The allowlist adds bounded read-only coverage for:

- TCP and UDP endpoints, owning PIDs, adapters, IP configuration, DNS servers, routes, and interface state;
- Windows services, bounded Application/System event-log reads, scheduled tasks, Authenticode signatures, and file ACLs;
- validated PID, port, address-family, TCP-state, and event-count parameters.

PowerShell CLIXML error output is normalized into readable stderr, while arbitrary scripts and pipelines remain unavailable.

## Runtime-owned process cancellation

Timeout and cancellation now terminate the runtime-owned process tree instead of only the direct child process. The caller still cannot provide an arbitrary PID, so cancellation remains scoped to processes PLA created for the active task.

## Governed Windows actions

v1.6.0 introduces the internal `windows.*` capability domain:

- `windows.action_status`
- `windows.flush_dns_cache`
- `windows.service_control_preflight`
- `windows.service_control`
- `windows.service_control_status`
- `windows.elevation_broker_restart`

System writes do not expand the generic PowerShell surface. DNS cache flush requires explicit confirmation. Service control additionally requires a transaction and an exact deployment-local service/operation rule in `config/windows_actions.local.json`.

Service actions are checked before queueing and again inside the Interactive Elevation Broker before UAC. Disabled or unstable services, services that cannot stop, and stop/restart requests with active dependent services are rejected. The caller cannot supply an executable, arbitrary command line, or arbitrary PowerShell script.

The local Windows action policy is ignored by Git and protected from ordinary PLA file access. The tracked `config/windows_actions.example.json` remains empty by default, so service control fails closed until a deployment intentionally authorizes an exact service and operation.

## External Completion Gate

External-pending transaction actions can now declare a fixed read-only completion verifier. The Transaction Envelope:

1. validates the completion capability immediately when the pending result is received;
2. requires that verifier to be read-only and free of confirmation/transaction requirements;
3. persists the verifier capability and arguments in transaction evidence;
4. prevents ordinary `succeeded` checkpoints from bypassing the completion requirement;
5. exposes `core.transaction_complete_external`, which can only invoke the verifier already recorded on the transaction step.

Windows service control and elevated software-migration install/uninstall flows use this Completion Gate.

Completion-gated running actions survive a PLA HTTP restart as resumable running steps with a new revision and preserved verifier contract. Ordinary running steps without a trusted completion contract continue to recover as interrupted and blocked.

## Interactive Elevation Broker lifecycle

The Elevation Broker records its current external launch while a UAC-mediated action is active. `windows.elevation_broker_restart` refuses to replace a busy broker, verifies the broker process identity before stopping it, relaunches only through the fixed project entrypoint, and verifies the replacement process before reporting success.

## Documentation

README now documents the Browser Provider's existing PLA-managed persistent browser profile and the new Windows action / Completion Gate boundaries.

## Validation

- Full v1.6.0 split regression: **602 / 602 passed**.
- Python compilation checks passed for the modified runtime, transaction, Windows action, Elevation Broker, software-migration, and server modules.
- Loaded-runtime E2E verified:
  - six live `windows.*` capabilities;
  - six live core transaction capabilities including `core.transaction_complete_external`;
  - structured `windows.service_control_preflight` on the local Print Spooler;
  - controlled PLA HTTP restart;
  - controlled Interactive Elevation Broker replacement and recovery to `WinSta0/Default`;
  - software-migration provider rediscovery after HTTP restart.
- No DNS flush, Windows service start/stop/restart, software install, or software uninstall was executed during release validation.
