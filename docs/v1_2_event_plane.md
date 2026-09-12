# PLA v1.2 Phase 1 — Event Core

## Goal

Introduce a durable cross-cutting event fact layer without changing PLA's control-flow model.

Phase 1 deliberately stops before Hook/Gate execution.

## Non-goals

Phase 1 does not implement:

- hook manifests;
- ALLOW / DENY gates;
- argument transformation;
- event-triggered capability dispatch;
- notifications;
- metrics exporters;
- plugin-side subscribers.

CapabilityBroker remains the explicit invocation owner.

## Event envelope

Every global event contains:

```text
sequence
schema_version
event_id
timestamp
event_type
source
subject
correlation_id
causation_id?

capability_id?
provider_id?
transaction_id?
task_id?

payload
payload_sha256
```

`sequence` is the SQLite autoincrement cursor. `event_id` is the stable event identity.

`correlation_id` groups facts belonging to one logical invocation. For CapabilityBroker
instrumentation, one validated invocation receives one correlation id.

`causation_id` points from the terminal event to its matching
`capability.before_invoke` event.

## Storage

The production store is:

```text
state/events.sqlite3
```

The database uses:

- SQLite WAL journal mode;
- synchronous FULL;
- one process-owner lock for the file-backed store;
- append-only event rows;
- indexes by event type, correlation, capability, and provider.

Global events are not pruned by the EventStore. Retention/compaction is a later concern and must
not be silently introduced into the first event schema.

## CapabilityBroker instrumentation

Instrumentation starts only after capability availability, confirmation/transaction policy, and
JSON-schema validation have succeeded.

Emitted events:

```text
capability.before_invoke
capability.succeeded
capability.failed
```

Validation/policy rejection before that boundary emits no runtime event in Phase 1.

`capability.failed` is emitted for provider/runtime exceptions and for normalized results whose
semantic status is one of:

```text
failed
error
timeout
precondition_failed
blocked
not_ready
```

External-pending results are successful invocations whose business work remains pending.

## Privacy and payload policy

The Event Plane does not persist raw capability input or output content.

Before events record:

- argument SHA-256;
- argument key names;
- risk level;
- confirmation required/supplied;
- transaction required/context flags.

Terminal events record:

- argument SHA-256;
- result SHA-256;
- normalized semantic status;
- is_error;
- emitted artifact ids.

Exception events record:

- exception type;
- exception-message SHA-256;
- exception-message length.

Raw exception text is not persisted.

## Failure policy

Event persistence is fail-open in Phase 1.

If EventStore emission fails, CapabilityBroker continues the selected capability invocation with
its pre-existing semantics. The Event Plane is observability, not a new source of execution
correctness.

## Query surface

The stable Capability Broker exposes:

```text
core.event_query
```

Supported filters:

- `after_sequence`;
- `limit`;
- `event_types`;
- `correlation_id`;
- `capability_id`;
- `provider_id`.

Results contain:

```text
events
next_cursor
has_more
returned_count
```

The query capability carries the `event-control` tag. CapabilityBroker does not emit global
events for capabilities with that tag, preventing event-query recursion.

## Existing local event stores

TaskStore and ActionTransactionStore already maintain local event histories.

Phase 1 does not migrate or duplicate those local state-machine events into the global EventStore.
Those stores remain the source of truth for task and transaction recovery.

Future phases may publish selected cross-cutting facts from those stores, but the global Event
Plane must never become a prerequisite for their correctness.

## Phase 1 acceptance

Phase 1 is accepted when all of the following hold:

1. EventEnvelope and append-only EventStore pass unit tests.
2. Capability success creates `before_invoke -> succeeded` with shared correlation and explicit causation.
3. Capability exception creates `before_invoke -> failed`.
4. Schema/policy rejection before invocation creates no event.
5. Raw arguments/results/exception messages are absent from broker event payloads.
6. EventStore failure does not change capability result semantics.
7. `core.event_query` can read filtered events without recording itself.
8. Events survive PLA HTTP restart.
9. Existing regression suite remains green.

## Validation evidence

Current Phase 1 validation:

- targeted Event/Core/Broker suite: **30 passed**;
- Event/Core/Broker + HTTP/release compatibility suite: **35 passed**;
- full PLA regression suite: **426 passed in 80.66 s**;
- live E2E emitted sequence 1/2 for `runtime.provider_status` with correlation
  `4175a3587a6d4f55a016bdeea4541e4b`;
- the terminal event's `causation_id` matched the `before_invoke` event id;
- `core.event_query` did not self-record;
- after a real PLA HTTP restart, querying the same correlation returned the same
  persisted sequence 1/2 events from `state/events.sqlite3`.
