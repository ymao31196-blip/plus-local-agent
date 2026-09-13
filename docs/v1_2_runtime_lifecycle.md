# PLA v1.2 Phase 5 — Runtime Lifecycle Plane

## Goal

Replace ad-hoc self-restart helpers with a narrow, auditable lifecycle control
plane. ChatGPT may request one exact PLA HTTP restart, but it does not gain
arbitrary process-management authority.

## Architecture

```text
ChatGPT
  -> runtime.restart_http (INVOKE)
  -> Capability Broker / Gate / Event
  -> state/lifecycle/<request>.request.json
  -> independent Runtime Lifecycle Broker
  -> restart_pla.ps1 -ExpectedPid <current-http-pid> -Json
  -> verify old listener ownership
  -> restart only PLA HTTP
  -> leave Secure MCP Tunnel running
  -> durable restart status
  -> ChatGPT reconnects and calls runtime.restart_status
```

The lifecycle broker is a separate process started by `start_all.ps1` and stopped
by `stop_all.ps1`. It uses a Windows named mutex to prevent duplicate brokers.

## Stable capabilities

```text
runtime.lifecycle_status
runtime.restart_http
runtime.restart_status
```

`runtime.restart_http` is privileged and requires explicit `INVOKE`.
It does not run inside an Action Transaction because the selected HTTP process is
expected to terminate during execution.

## Request safety

The HTTP process writes a versioned request containing only:

- a generated 32-character request id;
- fixed kind `restart_http`;
- creation and `not_before` timestamps;
- the exact current HTTP PID;
- fixed source `runtime.restart_http`.

The request file is written last and acts as the broker's commit marker.

The independent broker accepts no arbitrary executable, argv, shell command,
port, target PID, or script path from the request. It always invokes the
repository-owned `restart_pla.ps1` with the request's exact expected HTTP PID.

`restart_pla.ps1` independently verifies that port 8766 belongs to the expected
PID and that the process command line contains the PLA project root and
`server.py` before stopping it.

## Asynchronous semantics

Self-restart is intentionally asynchronous. The capability returns:

```json
{
  "status": "accepted",
  "request_id": "...",
  "state": "queued",
  "expected_http_pid": 1234,
  "not_before": "..."
}
```

The broker observes a short `not_before` grace period so the Capability response
and its success Event can be persisted before the HTTP process is terminated.

After the tunnel reconnects, `runtime.restart_status` is the source of truth for
whether the restart completed and which new PID was verified.

A dropped connection alone is never treated as proof of success.

## Lifecycle status

`runtime.lifecycle_status` reports:

- current HTTP PID and local port health;
- Secure MCP Tunnel health-port reachability;
- lifecycle broker PID/state/heartbeat freshness;
- current lifecycle request id, if any.

## Failure privacy

Lifecycle failures retain bounded structural metadata and SHA-256 hashes rather
than raw script stdout/stderr or exception text.

## Explicit non-goals

Phase 5 does not expose:

- arbitrary process start/stop/kill;
- arbitrary PowerShell;
- arbitrary service control;
- tunnel restart;
- machine reboot/shutdown;
- provider restart;
- background autonomous restart policy;
- restart retries.

## Acceptance

Phase 5 is accepted when:

1. lifecycle status distinguishes ready/stale/absent broker state;
2. restart requests fail closed if the independent broker is unavailable;
3. restart request ids and status lookup are bounded and validated;
4. the broker rejects unknown fields/kinds/sources/stale requests;
5. `restart_pla.ps1` refuses an unexpected HTTP PID;
6. restart capability requires `INVOKE`;
7. start/stop scripts manage the lifecycle broker with ownership checks;
8. a real ChatGPT/Tunnel E2E returns an accepted request id, disconnects only the
   HTTP process, reconnects through the unchanged tunnel, and reports a verified
   new HTTP PID through `runtime.restart_status`;
9. the existing regression suite remains green.

## Validation evidence

Current Phase 5 validation:

- Lifecycle/Provider/Observer targeted suite initially passed **31 tests**;
- dedicated Lifecycle suite after broker-liveness hardening passed **18 tests**;
- final full PLA regression after broker-liveness hardening passed **472 tests in 103.04 s**;
- `restart_pla.ps1` was executed with an intentionally wrong expected PID and refused the
  restart without touching the live HTTP process;
- modified `start_all.ps1` added the Runtime Lifecycle Broker as PID **28412** while leaving
  the existing HTTP PID **9108** and tunnel PID **884** unchanged;
- after one bootstrap restart loaded Phase 5, `runtime.lifecycle_status` reported broker
  **28412** ready, HTTP **25700**, and tunnel health online;
- a real `runtime.restart_http` request
  `7996d97313e74d4885c8c9f9bd9aa474` returned `accepted` before termination and later
  completed with HTTP **25700 -> 29432**, Lifecycle Broker **28412**, and tunnel PID **884**;
- a second native Lifecycle restart
  `d053781b0028489e9862bde401e64b04` completed with HTTP **29432 -> 19416** while the tunnel
  again remained PID **884**;
- stopping the Lifecycle Broker through `stop_lifecycle_broker.ps1` immediately produced
  `alive=false` and `ready=false` even though the last status file still said
  `state=running`, proving process-liveness validation works;
- a subsequent `start_all.ps1` invocation restarted only the Lifecycle Broker as PID
  **25884**, while HTTP remained **19416** and the tunnel remained **884**.
