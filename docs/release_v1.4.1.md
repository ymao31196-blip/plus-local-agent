# PLA v1.4.1

Released: 2026-09-13

PLA v1.4.1 is a focused Computer Use visibility patch on top of v1.4.0.

## Added

- User-visible Windows Computer Use activity indicator driven by PLA's existing
  Event/Observer plane.
- Distinct visible states for desktop observation and desktop control.
- Non-activating, click-through, topmost overlay that lingers briefly after a
  Computer capability completes.
- Computer Provider self-UI filtering so the indicator remains visible to the
  local user but is removed from Agent-facing Computer observations.

## Architecture

The indicator does not live inside the Computer Provider and does not change
Computer capability semantics:

```text
computer capability
    -> Event Plane
    -> computer-use-indicator Observer
    -> Windows overlay
```

The overlay is first-party local UI. Computer Provider responses recursively
filter the reserved indicator window identity so the Agent does not treat its own
status UI as an interaction target.

## Verification

- Targeted Computer/Observer/release tests: 26 passed.
- Full regression: 518 passed in 90.49s.
- Live Runtime Lifecycle restart preserved the Secure MCP Tunnel.
- `computer-use-indicator` registered alongside the built-in audit observer.
- Windows-native enumeration observed the indicator as visible while
  `computer.list_windows` returned zero matching indicator windows.

## Compatibility

- Computer backend remains Microsoft winapp CLI 0.5.0.
- No Computer capability was added or removed.
- No change to the v1.4.0 tag.
- No change to the ChatGPT-native Agent Loop architecture.
