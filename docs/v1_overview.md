# PLA architecture overview

## Production decision boundary

PLA uses a ChatGPT-native agent loop:

```text
ChatGPT = reasoning / planning / capability selection / next-step decisions
PLA     = local capability runtime / policy / state / execution / observations
```

PLA intentionally does not embed a second production planner. External providers expose
capabilities; ChatGPT composes them.

## Stable ChatGPT-facing surface

The preferred public surface remains:

```text
capability_search
capability_describe
capability_invoke
```

This prevents every downstream provider or new governance primitive from expanding the
ChatGPT MCP tool schema.

Built-in transaction governance is registered as the internal `core` provider:

```text
core.transaction_create
core.transaction_get
core.transaction_checkpoint
core.transaction_finalize
core.transaction_invoke
```

These capabilities reuse the same durable `TRANSACTION_STORE`, Capability Broker, and
Transaction Action Envelope as the legacy direct transaction MCP tools. They are adapters,
not a second transaction implementation.

## Provider plane

Provider installation and runtime declaration are separate:

- `provider_specs/<id>.txt`: exact dependency pins for isolated Python providers.
- `provider_manifests/<id>.json`: runtime declaration plus capability policy.
- `.provider_envs/<id>/`: ignored isolated Python environments.
- `isolated_python_stdio`: Python provider inside its dedicated environment.
- `executable_stdio`: reviewed native executable provider without a Python wrapper.

The current mainline includes reviewed MarkItDown, DOCX, PDF, Microsoft WinGet,
Windows Management, and Software Migration providers. Provider Doctor checks lifecycle health,
runtime availability, and pinned-version drift.

Tool discovery is dynamic but filtered through manifest allowlists and per-tool overrides.
A downstream tool that appears but is not allowlisted stays hidden.

### Provider hot-plug runtime

Provider lifecycle changes are exposed through an internal `runtime` provider on the same
stable Capability Broker surface:

```text
runtime.provider_status
runtime.provider_rescan
runtime.provider_reload
runtime.provider_enable
runtime.provider_disable
```

The hot-plug runtime serializes manifest changes and performs full manifest parsing before
mutating the active provider set. `rescan` computes add/change/remove differences, updates the
shared MCPClientManager, and refreshes only affected providers. A removed provider is removed
from both the manager and Capability Registry. A changed provider is hidden before its new
configuration is rediscovered, so stale capabilities are not left available.

Temporary enable/disable overrides live only in process memory. Persistent configuration remains
`provider_manifests/*.json` plus `PLA_EXTERNAL_PROVIDERS`. Mutating hot-plug operations require
explicit `INVOKE` confirmation and cannot install dependencies, discover arbitrary executables,
or bypass manifest allowlists and capability policy.

A live E2E added a temporary read-only WinGet-backed provider, disabled and re-enabled it,
changed its manifest and reloaded it, then deleted its manifest and rescanned it away while the
PLA HTTP PID remained unchanged.

## Durable local runtime

The local executor retains and extends the v0.8 foundations:

- named workspace roots and protected paths;
- bounded filesystem, search, process, Python, PowerShell, and Git capabilities;
- SQLite persistent Task Store;
- cursor-based incremental observations;
- controlled cancellation;
- SHA-256 optimistic file writes;
- atomic single-file patching;
- transactional multi-file ChangeSet;
- Artifact Plane with immutable hashes and provenance;
- durable Action Transaction Runtime;
- explicit capability confirmation and transaction gates.

The transaction store never chooses capabilities. The action envelope invokes exactly one
caller-selected capability, records target policy and argument/result hashes, and updates one
transaction step. Verification steps require an explicit checkpoint before final commit.

## Event Plane (v1.2 Phase 1)

The global Event Plane is an append-only SQLite fact log, not a replacement for explicit control
flow or existing durable stores.

```text
Capability Broker
    -> schema/policy validation
    -> Gate Hook evaluation
    -> capability.gate_denied | continue
    -> capability.before_invoke
    -> explicit invocation path
    -> capability.succeeded | capability.failed
```

Every EventEnvelope has a monotonically increasing sequence, stable event id, schema version,
timestamp, source, subject, correlation id, optional causation id, optional capability/provider/
transaction/task identifiers, JSON payload, and payload SHA-256.

The Capability Broker creates one correlation id per validated invocation. The terminal event
causes from the matching `before_invoke` event, making each invocation pair reconstructable
without handing control to an event bus.

The global store intentionally does not retain raw arguments, returned content, or exception
messages. Broker events contain argument/result hashes and bounded metadata. Event persistence
is fail-open so observability cannot silently become a new capability-availability dependency.

`core.event_query` provides cursor-based, filtered read access through the stable Capability
Broker surface. Because it carries the `event-control` tag, querying the event log does not
create new events.

Existing TaskStore and ActionTransactionStore local event tables remain untouched. They retain
their recovery/state-machine responsibilities; the global Event Plane records cross-cutting
facts for future audit, metrics, notification, and Hook/Gate subscribers.

## Observer Hook Plane (v1.2 Phase 2)

Observer Hooks consume EventEnvelope facts only after the corresponding event has been appended
to EventStore:

```text
EventStore.append
    -> ObserverHookRuntime.dispatch
    -> HookInvocationStore
```

The built-in `audit-observer` subscribes to Capability gate-denied/before/succeeded/failed events. Hook
dispatch order is deterministic by `hook_id`. Hook execution records are stored separately in
`state/hooks.sqlite3` with event identity, correlation, duration, status, result hash, and
hashed exception metadata.

Observer failure is fail-open. A failing observer cannot modify arguments, deny execution,
replace results, or schedule follow-up capabilities. The Capability Broker remains the explicit
control-flow owner.

Stable read-only inspection is provided by:

```text
core.hook_status
core.hook_invocation_query
```

Both are tagged `hook-control`; like `event-control`, they are excluded from EventStore
instrumentation and therefore cannot recursively create audit records.

## Gate Hook Plane (v1.2 Phase 3)

Gate Hooks run after the existing Capability policy and schema checks and before
`capability.before_invoke`:

```text
validated invocation
    -> GateHookRuntime.evaluate
    -> ALLOW -> capability.before_invoke -> provider
    -> DENY  -> capability.gate_denied
```

Reviewed in-process Gates receive a deep copy of the validated arguments and bounded invocation
metadata. They cannot mutate the real provider arguments, replace confirmation, replace
transaction policy, or select a different Capability.

The combining rule is deny-overrides. Gate failures are fail-closed, unlike Observer Hooks:
an exception, malformed Gate result, or decision-persistence failure prevents the Capability
from crossing the execution boundary. Decision records are append-only in
`state/gates.sqlite3`; raw arguments and raw exception text are not persisted.

Stable read-only inspection is provided by:

```text
core.gate_status
core.gate_decision_query
```

`event-control`, `hook-control`, and `gate-control` capabilities are excluded from Gate
evaluation and Event instrumentation so runtime diagnostics remain available during policy
incidents.

Phase 3 intentionally registers no default production policy Gate. The infrastructure therefore
does not alter existing Capability behavior until a reviewed Gate is explicitly registered.

## External Observer Plugin Plane (v1.2 Phase 4)

Phase 4 externalizes only the Observer side of the Hook plane. Reviewed manifests under
`observer_manifests/*.json` select isolated Python modules under
`.observer_envs/<observer-id>/`. Matching persisted EventEnvelope facts are passed as one JSON
object on stdin to:

```text
<observer-python> -m <reviewed-module>
```

The plugin returns one bounded JSON object on stdout. Invocation uses `shell=False`, a manifest
timeout capped at five seconds, a 64 KiB stdout bound, and a reduced subprocess environment.
External Observers still run with host-user authority; this is process/dependency isolation, not
an OS security sandbox.

Runtime lifecycle is exposed through `runtime.observer_status/rescan/reload/enable/disable`.
Mutating controls require explicit `INVOKE` and use `hook-control` so lifecycle operations do
not recursively create Event/Observer/Gate records. Manifest rescan can add, replace, disable,
re-enable, or remove an Observer without restarting PLA HTTP.

External Gate plugins, arbitrary executables, shell commands, automatic package installation,
network sandboxing, background delivery/retries, transformation, and plugin-triggered PLA
Capability calls remain outside Phase 4.

## Runtime Lifecycle Plane (v1.2 Phase 5)

Runtime self-maintenance is separated from the HTTP process that is being restarted:

```text
runtime.restart_http
    -> versioned restart request
    -> independent Runtime Lifecycle Broker
    -> restart_pla.ps1 -ExpectedPid <current-http-pid> -Json
    -> verify listener ownership
    -> restart PLA HTTP only
    -> keep Secure MCP Tunnel running
    -> durable restart status
```

The public lifecycle surface is `runtime.lifecycle_status`, `runtime.restart_http`, and
`runtime.restart_status`. Restart requires explicit `INVOKE`; it does not expose arbitrary
PID, executable, command, port, shell, service, or tunnel controls.

The HTTP process returns an accepted request id before termination. The lifecycle broker runs in
a separate process, validates the fixed request shape and age, verifies the expected current HTTP
PID through `restart_pla.ps1`, and records a durable terminal result. After reconnect,
`runtime.restart_status` reports the verified old/new HTTP PIDs and tunnel state.

Lifecycle status combines broker heartbeat freshness with actual broker-process liveness. This
prevents a freshly written but orphaned `state=running` status file from temporarily reporting
the lifecycle plane as ready.

`start_all.ps1` ensures the Lifecycle Broker alongside the existing elevation broker, HTTP, and
tunnel. During shutdown, `stop_all.ps1` closes tunnel ingress, then removes restart authority,
then stops HTTP.

## Interactive elevation plane

Windows UAC interaction is deliberately separated from the background HTTP runtime.

```text
transaction
-> reviewed elevated capability
-> elevation request in ignored runtime state
-> Interactive Elevation Broker in user Session / WinSta0 / Default desktop
-> Windows UAC
-> elevated executable
-> provider-specific verification
-> transaction checkpoint
```

The broker accepts narrowly validated request kinds rather than arbitrary commands. Current
reviewed shapes are a registered application uninstaller and an exact WinGet install command.
For WinGet installation, the broker reconstructs and verifies the full reviewed argv,
including exact package id, source, noninteractive flags, and absolute target directory.

Broker state lives under ignored `state/elevation/`. The broker is started by
`start_all.ps1` and can be managed independently through the dedicated start/stop scripts.

## Software migration E2E

The production migration path is now:

```text
assessment
-> snapshot-backed prepare
-> backup/precondition verification
-> confirmation + transaction gated uninstall
-> UAC through Interactive Elevation Broker
-> confirmation + transaction gated WinGet install
-> install-location verification
-> user-data verification
-> commit / rollback
```

A real Zotero E2E completed the entire sequence. Zotero 10.0.2 was migrated from the default
C-drive program location to `D:\Apps\Zotero`. Registry verification matched the requested
target, the old program directory was absent, and the full user-data manifest plus
`zotero.sqlite` SHA-256 remained identical to the verified pre-migration backup. The durable
transaction was finalized as committed.

## Structured Git boundary

Controlled Git staging now supports explicit modified files and non-ignored new regular files.
The safety properties remain:

- caller supplies every path explicitly;
- caller supplies the current SHA-256 for every staged file;
- expected HEAD must still match;
- Git index must start clean;
- merge/rebase/sequencer states disable structured staging/commit;
- raw bytes are hashed without clean filters;
- staged paths must equal the requested set exactly;
- structured commit uses compare-and-swap branch update;
- controlled `git_tag` creates only a new lightweight tag at the exact clean expected HEAD;
- controlled `git_push` requires `confirmation="PUSH"`, an existing named remote, the current branch, exact expected HEAD, and explicit matching tags;
- force push is unavailable; branch + tags are pushed atomically and verified with `ls-remote`;
- `core.git_tag` and `core.git_push` mirror these same operations through the stable Capability Broker and require explicit `INVOKE` confirmation, so release actions do not depend on host MCP-schema refresh.

## Security boundary

PLA is a policy-constrained local runtime, not an OS sandbox. Child processes execute with the
host user's authority unless a reviewed capability is explicitly elevated through the
Interactive Elevation Broker.

Important boundaries remain:

- protected local paths and named roots;
- provider and program allowlists;
- no arbitrary shell execution through the public capability surface;
- confirmation gates for privileged/destructive capabilities;
- transaction requirements for reviewed multi-step high-risk actions;
- ignored credentials, runtime state, local environments, logs, and workspaces;
- artifacts keep binary payloads out of model context.

The design goal is capability, auditability, and recoverability without moving planning
authority away from ChatGPT.
