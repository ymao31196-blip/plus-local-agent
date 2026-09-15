# PLA v1.5.0

Released: 2026-09-15

PLA v1.5.0 adds durable Human Takeover semantics for the interactive Browser and
Computer providers. The goal is not merely to stop issuing actions: it makes
human/agent ownership explicit, persists it across runtime restarts, blocks model
observation while the human is entering sensitive data, and requires a fresh
post-handoff observation before automation can regain control.

## Added

- A durable Human Takeover control plane stored under ignored runtime state.
- Explicit ownership states: `agent`, `human`, and `resync_required`.
- Top-level compatibility tools:
  - `human_takeover_begin`
  - `human_takeover_status`
  - `human_takeover_resume`
- Stable Capability Registry equivalents:
  - `core.human_takeover_begin`
  - `core.human_takeover_status`
  - `core.human_takeover_resume`
- Human Takeover enforcement through the existing fail-closed Gate Hook plane.
- Resynchronization completion through the existing Observer Hook plane.
- User-visible Windows overlay states for active takeover, resynchronization, and
  corrupted takeover state. Normal takeover/resync notices auto-hide after 15 seconds without
  changing the underlying safety lock; corrupt-state warnings remain persistent.

## Ownership semantics

Normal operation is:

```text
agent
  -> human_takeover_begin
human
  -> human finishes sensitive/manual interaction
  -> human_takeover_resume
resync_required
  -> successful trusted observation for each scoped provider
agent
```

While state is `human`, every capability on each scoped Browser/Computer
provider is denied, including screenshots and semantic inspection. This prevents
the model from reading credentials, 2FA codes, or other sensitive values while
the user has taken control.

After explicit resume, control remains blocked. Only observation-tagged read
capabilities are allowed for a provider that still requires resynchronization.
A successful trusted `browser.inspect` / `browser.screenshot` or
`computer.inspect` / `computer.screenshot` releases that provider. In a
multi-provider takeover, providers are released independently as each one is
resynchronized.

## Safety and recovery

- Resume uses optimistic revision checks and a takeover identifier.
- The top-level compatibility resume tool requires explicit `INVOKE` confirmation.
- `core.human_takeover_resume` also requires the Capability Broker's explicit
  `INVOKE` confirmation, so neither public resume path can silently reclaim
  control from the human.
- Human Takeover state is atomically persisted and survives PLA HTTP restarts.
- A corrupt/unreadable takeover state fails closed for Browser and Computer
  capability calls.
- A persisted or corrupt takeover state restores the local ownership overlay
  after runtime restart.
- Human Takeover events store bounded metadata and reason hashes rather than raw
  capability arguments or sensitive page/UI observations.
- The implementation reuses Gate and Observer extension points; the Capability
  Broker itself is not forked with provider-specific pause logic.

## Compatibility

- No change to the ChatGPT-native Agent Loop architecture.
- Browser still uses the independent persistent Playwright MCP runtime.
- Computer still uses the reviewed Microsoft winapp CLI backend.
- Existing provider manifests and Browser/Computer capability IDs are unchanged.
- Legacy top-level tools remain available while the stable Registry surface is
  preferred for agent orchestration.

## Verification

- Human Takeover unit, Broker integration, Gate/Observer, overlay, core Registry,
  and release-freeze tests pass.
- Real Broker-path tests verify active takeover denies both observation and
  control, resynchronization allows observation only, and successful fresh
  inspection releases control.
- Real Win32 overlay E2E verified the takeover banner is visible immediately, auto-hides after 15 seconds, and leaves the underlying takeover state unchanged.
- Final loaded-runtime E2E after `runtime.restart_http` verified `human` blocks Browser observation, the banner auto-hides while the lock remains active, `resync_required` blocks Browser control, and one trusted `browser.inspect` returns ownership to `agent`.
- Full regression: **554 passed in 89.82 s**.
