# PLA v1.4.2

Released: 2026-09-15

PLA v1.4.2 is a focused reliability patch on top of v1.4.1. It strengthens
Windows Computer Use foreground activation and makes first-time customer
installation portable, secret-safe, and reproducible from a clean clone.

## Added

- `computer.activate` as a separate top-level window activation primitive.
  `computer.focus` remains UIA control focus only.
- Verified Windows foreground ownership before activation reports success.
- A bounded DPI-aware semantic taskbar fallback for Windows foreground-lock
  cases such as SearchHost.
- Customer-oriented Windows `install.ps1` bootstrap with repository-local
  Python 3.11, reviewed Provider setup, regression validation, and optional
  Secure MCP Tunnel startup.
- Customer deployment documentation and secret-free Tunnel configuration
  template.

## Changed

- Tunnel startup now uses an explicit `PLA_TUNNEL_CONFIG` or ignored
  `config/tunnel.local.yaml`; the tracked configuration contains no
  deployment-specific Tunnel identity or credential material.
- The default local workspace now resolves relative to the active PLA
  repository, with `AGENT_WORKSPACE` retained as an explicit override.
- Python launcher discovery treats missing/incompatible `py.exe` candidates as
  probe misses instead of fatal installer errors.
- Provider setup and the installer judge nested Python/pip/npm/PowerShell
  commands by real exit codes while containing ordinary native stderr.
- Reviewed Node Provider specs are validated as line-oriented package lists;
  Browser bootstrap now installs `@playwright/mcp@0.0.80` correctly.

## Computer Use Safety

The new activation primitive does not weaken the existing foreground guards on
input operations. The normal flow is:

```text
computer.activate(window)
    -> verify OS foreground ownership
    -> computer.focus(control)
    -> computer.type/click
    -> post-action observation
```

When ordinary Win32 activation is blocked by Windows foreground policy, PLA may
use a semantic taskbar fallback only when the target mapping is unambiguous.
The fallback derives its target from the taskbar UIA tree, does not expose
caller-selected coordinates, restores the pointer position, and still requires
the original target HWND to become foreground before reporting success.

## Verification

- Targeted Computer/installer/provider release tests passed.
- Full regression: 536 passed.
- Real Computer Use E2E passed across SearchHost foreground lock:
  `activate -> focus -> type -> value -> clear -> restore`.
- Clean customer-install E2E was executed from a fresh clone with no `.venv`,
  no `.provider_envs`, and no local Tunnel configuration.
- The clean install created all reviewed Provider environments, including:
  - `@playwright/mcp@0.0.80`
  - `@microsoft/winappcli@0.5.0`
- Installer-owned regression run: 536 passed in 94.48s.
- Main environment and every Python Provider environment passed `pip check`.
- The clean install finished with `status: PARTIAL` only because no customer
  Tunnel ID/credential was supplied; Provider installation, WinGet runtime, and
  regression validation were ready.
- The validation clone and release repository both remained Git-clean.

## Compatibility

- Computer backend remains Microsoft winapp CLI 0.5.0.
- Browser backend remains Microsoft Playwright MCP 0.0.80.
- Computer Provider now exposes 18 controlled capabilities, adding
  `computer.activate`.
- No change to the ChatGPT-native Agent Loop architecture.
- v1.5 remains reserved for Human Takeover / pause semantics.
