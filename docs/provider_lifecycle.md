# Provider Lifecycle and Doctor (v0.18)

PLA v0.18 adds lifecycle state and diagnostics for external MCP providers without
introducing automatic business-call retries.

## Lifecycle states

A configured provider can be observed as:

- `configured`: registered from its manifest but not yet discovered.
- `ready`: discovery or a healthy transport call succeeded.
- `error`: discovery failed.
- `degraded`: a previously ready provider hit a transport/protocol/timeout error.

Intentional disablement is represented separately by `enabled: false` and is
reported by Provider Doctor as `disabled`, not as a failure.

Lifecycle metadata includes:

- `last_discovered_at`
- `last_success_at`
- `last_failure_at`
- `last_latency_ms`
- `consecutive_failures`
- `retry_after`
- `error_type`
- `error_message`

## Retry policy

Discovery and live health probes use exponential backoff after failure. A forced
Doctor probe can bypass the backoff and refresh the provider.

PLA does **not** transparently retry `tools/call`. This is deliberate: write or
external-side-effect capabilities must never be replayed automatically.

A remote MCP `ToolError` is treated as a business/tool failure and does not mark
the whole provider unhealthy. Transport, protocol, connection, and timeout errors
mark the provider `degraded` and make its registered capabilities unavailable
until discovery succeeds again.

## Provider Doctor

The `provider_doctor` MCP tool can inspect one provider or all configured
providers.

Default mode performs static checks:

- manifest exists
- provider spec exists
- isolated Python interpreter exists
- configured cwd exists
- exact package pins in `provider_specs/<id>.txt`
- installed package versions
- version drift
- current lifecycle state

With `live_probe=true`, Doctor also performs a live MCP discovery probe
(`tools/list`) and refreshes the Registry if it succeeds.

`force=true` bypasses lifecycle retry backoff for that live probe.

Provider Doctor never replays a business capability invocation.
