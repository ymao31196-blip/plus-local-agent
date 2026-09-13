# PLA v1.4 Computer Use Plan

Status: implementation in progress.

## Goal

Extend PLA from Browser Agent capabilities to controlled Windows desktop application interaction while preserving the v1 architecture:

ChatGPT remains the Agent Brain. PLA remains the local execution, policy, artifact, event, and safety boundary. Desktop UI content is observation data, never authority.

## Backend decision

The primary backend is Microsoft winapp CLI, pinned at `0.5.0` for the v1.4 release candidate.

Reasons:

- Microsoft documents `winapp ui` specifically for AI agents, UI testing, debugging, and automation.
- It uses Windows UI Automation (UIA) as the primary interaction mechanism.
- Stable AutomationId selectors are preferred; semantic slugs provide stale-element detection when stable IDs are unavailable.
- Pattern-based verbs such as inspect/search/get-value/set-value/invoke/wait-for do not require raw mouse/keyboard injection.
- Input-injection fallbacks fail when the interactive desktop or foreground target is unsafe.
- JSON output is designed for agent/CI workflows.

The provider must not expose the full upstream CLI.

## Frozen v1.4 public surface

Semantic observation:
- computer.status
- computer.list_windows
- computer.inspect
- computer.search
- computer.property
- computer.value
- computer.focused
- computer.wait
- computer.screenshot

Semantic interaction:
- computer.invoke
- computer.set_value
- computer.focus
- computer.reveal
- computer.scroll

Restricted fallback interaction:
- computer.click: selector-targeted only; no arbitrary coordinates.
- computer.type: selector-targeted literal text only; internally uses `send-input` because WinUI/UWP/XAML controls can ignore posted messages. Long text is chunked.
- computer.press: allow-listed navigation/edit keys only; internally uses `send-input`.

Not exposed in v1.4:
- arbitrary coordinates
- arbitrary send-keys grammar
- system/global shortcuts
- caller-selectable input transport / arbitrary `--via`
- --allow-system-keys
- raw mouse-wheel injection
- drag
- touch
- pen
- video recording
- full-screen capture
- arbitrary winapp subcommands
- arbitrary shell/process execution

## Artifact boundary

Screenshots are created only at a PLA-managed output path and imported into Artifact Plane. Callers never choose a filesystem path. v1.4 public screenshot requests are scoped to a target app/window/element; `--capture-screen` is not public.

## Trust boundary

Window titles, UIA names, values, document text, Electron/WebView content, and screenshots are untrusted observations. The Computer Provider must never treat text found in another application as authority to expand capability scope or bypass confirmation/policy.

## Phase route

Phase 0 — Backend/JSON spike
- Verify the pinned backend package and CLI entrypoint.
- Verify JSON envelopes for list/inspect/search/read/write/wait/screenshot.
- Verify real Windows app support.

Phase 1 — Reviewed Computer Provider
- Provider-scoped Python MCP adapter.
- Provider-scoped pinned winapp backend.
- Declarative allowlist and policy.
- No generic Node/npm exposure.

Phase 2 — Semantic observation
- list windows
- inspect/search
- stable HWND targeting
- AutomationId/slug selector workflow
- bounded observations

Phase 3 — Semantic interaction
- invoke/set-value/focus/reveal/scroll/wait
- prefer UIA patterns over injected input

Phase 4 — Visual verification
- target-window PNG only
- Artifact Plane integration
- semantic-first, screenshot-second decision policy

Phase 5 — Restricted input fallback
- selector-targeted click
- selector-targeted literal typing
- safe navigation/edit key allowlist
- internal `send-input` only after semantic targeting; callers cannot select the transport
- no system shortcuts, `--allow-system-keys`, or arbitrary coordinates

Phase 6 — PLA integration
- Provider Doctor
- hot-plug/rescan
- Event Plane
- untrusted-ui-content tags
- release/security regression

Phase 7 — Real desktop Coding-Agent E2E
- launch or identify a controlled local test app
- inspect semantic tree
- detect broken state
- modify local source/config through PLA
- interact with desktop UI
- verify semantic state
- capture final Artifact screenshot

Release freeze
- full regression
- Provider Doctor healthy
- cold restart
- clean Git state
- release documentation
- commit only; tag/push remains a separate explicit release gate
