# PLA v1.8.0

PLA v1.8.0 consolidates the runtime around a smaller top-level MCP surface while expanding the dynamic Capability Registry. It also adds source-backed WPS Office integration, the Skill Library Provider, governed WSL/LaTeX execution, and formally removes the retired MCP Sampling path.

## Smaller top-level MCP surface

The runtime now exposes 23 top-level MCP tools instead of 55.

The direct surface is reserved for hot-path and recovery-friendly operations such as:

- capability search/describe/invoke;
- common file reads and writes;
- bounded process and PowerShell execution;
- artifact export;
- task submission/result/cancellation;
- Git status and diff;
- runtime diagnostics.

Lower-frequency or specialized operations are discovered dynamically through the Capability Registry.

At the v1.8.0 release point, the live registry contains 393 dynamic capabilities.

## Core capability consolidation

Git, Project, Transaction and Artifact-management operations have been moved behind stable `core.*` capability IDs.

Notable Git routes include:

- `core.git_stage`
- `core.git_remove`
- `core.git_commit`
- `core.git_tag`
- `core.git_push`

`core.git_remove` stages only explicit tracked files that are already absent from the worktree and requires an exact expected HEAD plus a clean index.

Artifact management is also dynamic. Read-only cleanup preview is separated from destructive cleanup/revocation, and destructive operations require explicit confirmation.

## WPS Office and source-backed Providers

PLA now supports source-backed Git/npm Provider installation with:

- exact upstream Git commit pinning;
- repository-owned compatibility patches;
- fixed build entrypoints;
- fresh-install support through the normal Provider setup path.

The reviewed WPS Office Provider is based on `lc2panda/wps-skills` and exposes 250 capabilities through the Capability Registry.

PLA applies a tracked Windows COM compatibility patch before build. WPS Office is now the primary Office automation surface; the legacy standalone DOCX Provider remains available in source form but no longer autostarts.

## Skill Library Provider

The Skill Library Provider exposes four capabilities for listing, searching, reading and refreshing reusable `SKILL.md` guidance.

Skills are advisory experience rather than an execution gate. They are intended to reduce recurring mistakes, preserve stable workflows and surface easy-to-forget conventions while leaving current user instructions, task facts and ChatGPT's judgment in control.

## Local execution additions

The governed local process surface now includes:

- WSL / `wsl.exe`;
- `latexmk` / `latexmk.exe`;
- `xelatex` / `xelatex.exe`.

These continue to run through PLA's bounded process policy rather than arbitrary shell execution.

## MCP Sampling retired

The MCP Sampling experiment has been removed from the runtime.

Removed components include:

- `probe_sampling`;
- `run_agent_task`;
- the Sampling backend;
- Sampling-specific tests and runtime state.

PLA no longer contains a server-side model loop. ChatGPT remains the Agent Brain and decides the next tool or capability call; PLA remains the bounded local execution runtime.

Historical release documents continue to record that Sampling experiments existed in earlier versions.

## Validation

- Full regression: **658 / 658 passed**.
- Live runtime: **23 top-level MCP tools**.
- Live Capability Registry: **393 dynamic capabilities**.
- External Providers active at release: **9**.
- WPS Office Provider: **250 capabilities**, healthy.
- Skill Library Provider: **4 capabilities**, healthy.
- WPS E2E verified workbook creation, range write, formatting, formula evaluation, save and read-back.
- Python compilation checks passed for modified runtime, Git, routing and Provider modules.
