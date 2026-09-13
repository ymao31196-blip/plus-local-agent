# PLA v1.2 Phase 2 — Observer Hooks

## Goal

Add deterministic, fail-open event subscribers on top of the Phase 1 Event Plane without
introducing execution gates or event-driven control flow.

Observer Hooks are consumers of already-persisted EventEnvelope facts. They cannot alter
capability arguments, approve or deny execution, replace results, or schedule follow-up
capabilities.

## Runtime order

The Capability Broker remains the control-flow owner:

```text
schema / policy validation
        ↓
EventStore.emit(...)
        ↓
ObserverHookRuntime.dispatch(event)
        ↓
existing capability invocation path
        ↓
EventStore.emit(terminal event)
        ↓
ObserverHookRuntime.dispatch(event)
```

An observer is called only after the corresponding EventEnvelope has been durably appended to
the EventStore.

Hook dispatch does not produce another EventEnvelope. Hook execution records live in a separate
store, preventing recursive event/hook chains.

## Observer Hook Runtime

`ObserverHookRuntime` maintains a deterministic in-process registry:

```text
hook_id
event_types
enabled
handler
```

Phase 2 supports exact event-type matching and the reserved wildcard `*`.

Selected hooks execute in sorted `hook_id` order for deterministic tests and audit behavior.

The production runtime currently registers one built-in hook:

```text
audit-observer
  capability.before_invoke
  capability.succeeded
  capability.failed
```

The built-in Audit Hook returns only an attestation over the already-persisted event identity and
hash metadata.

## Hook Invocation Store

Observer execution records are stored independently at:

```text
state/hooks.sqlite3
```

The database uses SQLite WAL and synchronous FULL.

Each invocation record contains:

```text
sequence
invocation_id
hook_id
event_id
event_sequence
event_type
correlation_id
started_at
finished_at
duration_ms
status
result_sha256?
error_type?
error_message_sha256?
error_message_length?
```

Raw Hook return values and raw exception text are not persisted.

The store has no public update/delete mutation API; Phase 2 records are append-only.

## Failure semantics

Observer Hooks are always fail-open in Phase 2.

A Hook may:

- complete successfully;
- raise an exception;
- return a non-JSON-serializable value;
- fail while its invocation record is being persisted.

None of these conditions can change the selected Capability's result or exception semantics.

Hook failures are recorded when the Hook Invocation Store is available. Error text is represented
only by type, SHA-256, and length.

This is intentionally different from the future Gate Hook model. Phase 3 may introduce explicit
ALLOW / DENY semantics, but Phase 2 observers cannot deny anything.

## Stable read surface

Two read-only capabilities are exposed through the existing stable Capability Broker:

```text
core.hook_status
core.hook_invocation_query
```

Both carry the `hook-control` tag.

CapabilityBroker excludes `hook-control` and `event-control` capabilities from EventStore
instrumentation. Therefore Hook/Event inspection creates neither new runtime events nor new Audit
Hook invocation records.

`core.hook_invocation_query` supports:

- `after_sequence`;
- `limit`;
- `hook_id`;
- `event_id`;
- `event_type`;
- `correlation_id`;
- `status`.

## Explicit non-goals

Phase 2 does not implement:

- ALLOW / DENY decisions;
- argument transformation;
- confirmation replacement;
- transaction replacement;
- external Hook manifests;
- Hook hot-plug;
- subprocess Hook execution;
- third-party Hook packages;
- Hook-triggered Capability invocation;
- async/background observer delivery;
- retries;
- network notification Hooks.

The first version intentionally uses reviewed in-process observers only.

## Phase 2 acceptance

Phase 2 is accepted when all of the following hold:

1. HookInvocationStore records and queries durable observer executions.
2. Hook records survive store reopen.
3. Event matching and dispatch order are deterministic.
4. Audit Hook observes persisted EventEnvelope ids and hashes.
5. Hook failure is persisted without raw exception text.
6. Hook failure never changes Capability success/failure behavior.
7. Broker persists an Event before dispatching its observer.
8. `core.hook_status` and `core.hook_invocation_query` do not self-observe.
9. Real PLA HTTP E2E produces matching EventStore and Audit Hook records.
10. Hook invocation records survive PLA HTTP restart.
11. Existing regression suite remains green.

## Validation evidence

Current Phase 2 validation:

- Observer/Event/Broker/Core targeted suite: **38 passed**;
- Observer/Event/Broker/Core + HTTP/release compatibility suite: **43 passed**;
- full PLA regression suite: **434 passed in 77.45 s**;
- live `runtime.provider_status` E2E produced EventStore sequences **3/4** under
  correlation `427b65b0209d40118dd3f37e79a41a58`;
- `audit-observer` produced Hook Invocation Store sequences **1/2** for the same
  correlation and the same two event ids;
- `core.hook_status` reported exactly one enabled built-in observer;
- `core.hook_status` and `core.hook_invocation_query` produced no recursive
  EventStore or Hook Invocation Store records;
- after a real PLA HTTP restart, EventStore sequences **3/4** and Hook Invocation
  Store sequences **1/2** remained queryable under the same correlation.
