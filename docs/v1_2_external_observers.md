# PLA v1.2 Phase 4 — External Observer Plugins

## Goal

Allow reviewed Observer plugins to be declared and hot-plugged without restarting
PLA HTTP while preserving the production decision boundary:

```text
ChatGPT owns planning and Capability selection.
PLA owns local policy, execution, audit, and plugin isolation.
External Observers may observe persisted EventEnvelope facts only.
```

Phase 4 does not externalize Gate Hooks.

## Why Observer first

FastMCP middleware supports request interception and denial, but middleware is an
implementation-level feature rather than part of the MCP protocol. PLA therefore
keeps Hook/Gate behavior inside its local runtime rather than creating a new MCP
protocol extension.

External code is materially more dangerous than the built-in audit observer. A
third-party Observer process still runs with host-user authority and can have side
effects outside PLA unless the operating system constrains it. For this reason the
first external plugin surface is observation-only; third-party Gate authority is
deferred until a stronger trust/sandbox model exists.

## Manifest

External Observer manifests live under:

```text
observer_manifests/*.json
```

Schema version 1 declares:

```json
{
  "schema_version": 1,
  "id": "example",
  "autostart": true,
  "event_types": ["capability.succeeded"],
  "timeout_seconds": 2.0,
  "runtime": {
    "kind": "isolated_python_module",
    "python": ".observer_envs/example/Scripts/python.exe",
    "module": "example_observer",
    "cwd": "."
  }
}
```

The Python interpreter must be inside the observer's dedicated
`.observer_envs/<id>/` directory. Arbitrary Python `-c`, script paths, shell
strings, environment injection, and arbitrary executables are not part of the v1
manifest.

## Invocation contract

For every matching already-persisted EventEnvelope, PLA launches:

```text
<observer-python> -m <reviewed-module>
```

with exactly one JSON EventEnvelope on stdin. The plugin returns exactly one JSON
object on stdout.

The runtime uses:

- `shell=False`;
- a maximum 5 second manifest timeout;
- a 64 KiB stdout bound;
- a reduced environment containing only basic Windows/process variables plus
  UTF-8 Python settings;
- no raw Capability arguments or results, because the Event Plane never stored
  them in the first place.

Observer failures remain fail-open relative to Capability execution and are
recorded through the existing HookInvocationStore.

## Hot-plug control

The stable Capability Broker exposes:

```text
runtime.observer_status
runtime.observer_rescan
runtime.observer_reload
runtime.observer_enable
runtime.observer_disable
```

Mutation operations require explicit `INVOKE` confirmation. All controls carry
`hook-control`, so they do not recursively create Event/Observer/Gate records.

Persistent selection comes from `PLA_EXTERNAL_OBSERVERS` and manifests.
Enable/disable overrides are process-local and disappear after restart.

## Explicit non-goals

Phase 4 does not implement:

- external Gate plugins;
- arbitrary in-process third-party Python imports;
- arbitrary executable observers;
- shell observer commands;
- observer network sandboxing;
- automatic package installation;
- plugin-triggered PLA Capability calls;
- async/background observer delivery;
- retries or delivery queues;
- argument/result transformation.

## Acceptance

Phase 4 is accepted when:

1. manifest validation rejects path escape, arbitrary Python execution modes, and
   invalid event/timeout declarations;
2. selected external Observers register under deterministic `external.<id>` hook ids;
3. matching EventEnvelope facts reach the external runner;
4. external Observer results and failures flow through HookInvocationStore;
5. rescan/reload/enable/disable work without PLA HTTP restart;
6. removed manifests remove their Hook registration;
7. runtime Observer controls do not self-observe;
8. no configured external Observer preserves current behavior;
9. the existing regression suite remains green.

## Validation evidence

Current Phase 4 validation:

- external Observer / Hook / Provider hot-plug targeted suites pass;
- full PLA regression suite: **454 passed in 100.55 s**;
- live HTTP/Tunnel E2E loaded Phase 4 with no configured external Observer;
- a temporary `e2e-observer` was enabled from a local manifest without HTTP restart;
- a real `runtime.provider_status` success Event was processed by
  `external.e2e-observer` in an isolated Python subprocess with Hook status
  `completed`;
- disabling the Observer immediately stopped external Hook records while the built-in
  Audit Observer continued normally;
- after editing the manifest subscription from `capability.succeeded` to
  `capability.before_invoke`, `runtime.observer_reload` applied the change live and the
  next invocation was observed on the new event type;
- deleting the temporary manifest followed by `runtime.observer_rescan` returned
  `removed=["e2e-observer"]` and removed its Hook registration;
- PLA HTTP stayed on PID **9108** and the Secure MCP Tunnel stayed on PID **884** for the
  entire enable/disable/reload/remove sequence;
- temporary E2E environment, module, and manifest were cleaned up afterward.
