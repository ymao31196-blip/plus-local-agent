# PLA v1.5.3

PLA v1.5.3 supersedes v1.5.2 with Computer Use restored to the v1.4.2 behavior boundary while
retaining the v1.5.1 Workspace Registry and controlled GitHub CLI execution.

## Computer Use rollback boundary

The Computer Use implementation is restored to the last release before Human Takeover was
introduced. It retains the v1.4.2 window-focus fixes:

- verified `computer.activate` foreground ownership;
- the DPI-aware semantic taskbar fallback for Windows foreground-lock cases;
- the observation-only Computer Use activity indicator.

The durable explicit Human Takeover control plane introduced in v1.5.0 is not part of this
release. Its top-level and `core` capabilities, Gate/Observer hooks, persisted ownership state,
resynchronization flow, and takeover overlay states are absent.

The automatic physical keyboard/mouse intervention detector introduced in v1.5.2 is also absent.
PLA does not install its low-level input hook or automatically change Computer Use ownership in
response to local input.

## GitHub CLI allowlist

The reviewed local process allowlist includes `gh` and `gh.exe`. This permits GitHub CLI to run
through the existing bounded `run_process` capability without exposing an arbitrary shell or
changing the controlled `core.git_tag` and `core.git_push` release gates.

## Upgrade and rollback relationship

This release keeps the v1.5.1 Workspace Registry and its source-isolation policy, applies the
GitHub CLI allowlist change, and selectively restores the Computer Use/Human Takeover files and
public capability surface to the v1.4.2 boundary. Historical v1.5.0 and v1.5.2 release records
remain documentation of those releases, not descriptions of the active v1.5.3 capability set.

## Validation

Release-freeze, Computer Use indicator, and foundation tests cover the v1.5.3 version boundary,
the rollback capability surface, the v1.4.2 indicator behavior, and GitHub CLI allowlist policy.
GitHub CLI E2E verified `gh 2.96.0` through controlled `run_process`. The final source regression
completed with **544 passed in 89.43 s**. The focused release, Computer Use, and foundation
regression completed with **100 passed in 26.89 s**.
