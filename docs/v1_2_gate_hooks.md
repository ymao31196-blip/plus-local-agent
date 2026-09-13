# PLA v1.2 Phase 3 — Gate Hooks

## Goal

Add a reviewed pre-invocation Gate Hook plane that can explicitly ALLOW or DENY a
validated Capability call without moving planning authority away from ChatGPT.

Gate Hooks are an additional policy layer. They do not replace or weaken existing
Capability availability, manifest allowlists, confirmation requirements,
transaction requirements, artifact policy, or JSON-schema validation.

## Placement

The Capability Broker order is:

```text
availability / manifest policy
-> confirmation policy
-> transaction policy
-> artifact contract checks
-> JSON-schema validation
-> Gate Hook evaluation
-> capability.before_invoke
-> provider/internal handler
-> capability.succeeded | capability.failed
```

A Gate denial occurs before `capability.before_invoke` because the selected
Capability never begins execution. The denial is instead recorded as
`capability.gate_denied`.

## Gate context

Reviewed in-process Gate Hooks receive a deep copy of the validated invocation
context:

```text
correlation_id
capability_id
provider_id
risk_level
tags
requires_confirmation
confirmation_supplied
requires_transaction
transaction_context
arguments_sha256
argument_keys
arguments
```

The real provider arguments are never handed to a Gate by reference. Argument
transformation is not supported.

Gate decision persistence stores hashes and bounded metadata, not raw arguments.

## Decision contract

A Gate returns:

```json
{"decision":"allow","reason_code":"policy_ok"}
```

or:

```json
{"decision":"deny","reason_code":"policy_block"}
```

Registered Gates execute in deterministic `hook_id` order. The combining rule
is deny-overrides: any DENY blocks the Capability.

Phase 3 does not register a default production policy Gate, so enabling the Gate
runtime itself does not change existing Capability behavior.

## Failure semantics

Observer Hooks are fail-open; Gate Hooks are deliberately fail-closed.

A Gate exception, malformed Gate result, or Gate decision-persistence failure is
treated as DENY. Raw exception text is never persisted; only exception type,
SHA-256, and message length are recorded.

This distinction is intentional:

- Observer failure must not change execution.
- Gate failure must not silently bypass an active security policy.

## Storage

Gate decisions are stored under ignored runtime state:

```text
state/gates.sqlite3
```

The store uses SQLite WAL mode, synchronous FULL, an owner lock, append-only
records, and cursor-based queries.

Stable read-only inspection is exposed through:

```text
core.gate_status
core.gate_decision_query
```

Both use the `gate-control` tag. Event, Hook, and Gate control capabilities are
excluded from global Event instrumentation and Gate evaluation so diagnostics
remain available during policy incidents.

## Relationship to MCP authorization

Gate Hooks are not a replacement for MCP transport authorization or OAuth.
Transport/client authorization decides who may reach the server. PLA Gate Hooks
decide whether an already-authenticated and already-validated local Capability
invocation may cross the final execution boundary.

## Explicit non-goals

Phase 3 does not implement:

- argument transformation;
- result transformation;
- replacement of confirmation or transaction policy;
- external Gate manifests;
- Gate hot-plug;
- third-party Gate packages;
- Gate-triggered Capability invocation;
- asynchronous Gate evaluation;
- network policy calls;
- automatic policy generation by ChatGPT.

## Phase 3 acceptance

Phase 3 is accepted when:

1. GateDecisionStore records, queries, and survives reopen.
2. Gate order is deterministic.
3. deny-overrides aggregation is enforced.
4. Gate exceptions fail closed without persisting raw exception text.
5. Gate handlers cannot mutate real provider arguments.
6. Gate denial prevents provider/internal handler execution.
7. Gate denial emits `capability.gate_denied`, not
   `capability.before_invoke`.
8. Observer Hooks can observe the already-persisted denial event.
9. No registered Gates preserve existing runtime behavior.
10. Gate-control inspection does not recursively enter Event/Hook/Gate control.
11. Existing regression suite remains green.

## Validation evidence

Current Phase 3 validation:

- Gate/Observer/Broker/Core targeted suite: **39 passed**;
- full PLA regression suite: **443 passed in 75.88 s**;
- critical Gate/Broker/Core/Observer/Server modules pass `py_compile`;
- latest-source registry construction exposes both `core.gate_status` and
  `core.gate_decision_query` as available `gate-control` capabilities;
- Gate denial, fail-closed error handling, argument-copy isolation, observer
  visibility, no-Gate compatibility, and Gate-control recursion bypass are all
  covered by dedicated tests;
- after a real PLA HTTP restart through the existing Secure MCP Tunnel,
  `core.gate_status` and `core.gate_decision_query` were available online;
- Gate-control inspection produced no recursive Event, Observer, or Gate records:
  Event cursor remained **4**, Observer cursor remained **2**, and Gate cursor
  remained **0**;
- a live `runtime.provider_status` invocation then produced EventStore sequences
  **5/6** and matching Audit Observer sequences **3/4**, while Gate decisions
  remained empty because no production Gate is registered;
- the same live status call reported all six reviewed providers ready.
