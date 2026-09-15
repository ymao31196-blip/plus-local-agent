# PLA v1.5.2

PLA v1.5.2 makes Human Takeover automatically detect local physical intervention during
Browser/Computer automation while preserving the explicit resume and resynchronization boundary.

## Automatic Human Takeover

A first-party `HumanInputMonitor` is attached to the existing Event/Observer plane. Browser and
Computer capability lifecycle events arm the monitor while interactive automation is in flight and
for a short handoff window immediately afterward.

On Windows, a dedicated low-level keyboard/mouse hook thread classifies local input:

- physical keyboard key-down events trigger takeover immediately;
- physical mouse button and wheel input trigger immediately;
- mouse movement must exceed a small distance threshold to filter device jitter;
- keyboard/mouse events carrying Windows injected-input flags are ignored.

The hook callback never performs the durable state transition directly. It queues a bounded signal
to a worker thread, which re-checks ownership and then calls the existing
`HumanTakeoverController.begin`. This keeps the Windows hook path short and preserves the existing
durable Human Takeover state machine as the single source of ownership truth.

Automatic intervention scopes both `browser` and `computer` so one interactive provider cannot
continue while the local user is taking control through the other. If an explicit partial takeover
is already active, detected physical intervention expands that same durable takeover to both
providers. If physical input arrives during `resync_required`, the state returns to `human`, clears
pending resynchronization, and keeps automation blocked until the user explicitly resumes again.

## Resume semantics remain explicit

Automatic detection changes only the entry path:

```text
agent
  -> physical user input detected
  -> human
  -> explicit INVOKE resume
  -> resync_required
  -> trusted fresh observation
  -> agent
```

PLA never automatically resumes control after physical input. Manual credentials, 2FA, payments,
or other sensitive interactions remain hidden from model observation during `human` state.

## Privacy and safety

The detector does not persist raw keyboard or mouse payloads. No key codes, typed text, or pointer
coordinates are written to PLA state or Event Plane records. Only bounded detector health and
trigger-kind metadata are exposed.

`core.human_input_monitor_status` reports whether detection is enabled, whether the Windows hook
is installed, whether the detector is armed, and any hook setup error. Automatic detection is on
by default on Windows and can be explicitly disabled before startup with
`PLA_AUTO_HUMAN_TAKEOVER=0`.

## GitHub CLI allowlist

The reviewed local process allowlist now includes `gh` and `gh.exe`, enabling controlled GitHub
CLI execution through the existing `run_process` policy without introducing arbitrary shell
execution.

## Validation

Automated tests cover provider arming, injected-input rejection, thresholded mouse movement,
immediate key/button/wheel intervention, concurrent takeover races, shutdown behavior, and detector
status policy. A real Windows hook smoke test verified successful install/status/uninstall while
the detector was unarmed. GitHub CLI allowlist E2E verified `gh 2.96.0` through controlled
`run_process`. Final source regression: **574 passed in 92.40 s**. A dedicated worker-thread test also verifies that an automatic partial-takeover expansion persists `human_takeover.expanded` to the Event Store.
