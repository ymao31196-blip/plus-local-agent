# Customer Installation

This document defines the supported Windows customer deployment path for PLA.

## Prerequisites

- Windows 10/11 x64
- Git for Windows
- Python 3.11
- current Node.js with npm
- Microsoft Edge
- WinGet MCP runtime (`WindowsPackageManagerMCPServer.exe`) for full `PASS`; without it the installer reports `PARTIAL`
- a customer-specific Secure MCP Tunnel ID, tunnel-client, and credential

## Recommended install

Clone the repository, check out the intended release tag, then run:

```powershell
.\install.ps1
```

The installer creates `.venv`, installs pinned core/document/test dependencies, installs reviewed
Provider environments, checks native prerequisites, and runs the PLA regression suite. Without
a customer Tunnel it ends as `PARTIAL`; this is intentional.

Complete the customer Tunnel and start the runtime with:

```powershell
.\install.ps1 `
  -TunnelId "tunnel_CUSTOMER_ID" `
  -TunnelClient "C:\path\to\tunnel-client.exe" `
  -TunnelCredential "C:\path\to\control-plane-api-key.txt" `
  -PersistEnvironment `
  -Start
```

`config/tunnel.local.yaml` is generated locally and ignored by Git. Do not copy another
deployment Tunnel ID or credential. The tracked example file is documentation only.


## Local workspace authorization

Customer workspace roots are deployment-local configuration, not PLA source code.

Use `config/workspaces.example.yaml` as a template and store the machine-specific registry in:

```text
config/workspaces.local.yaml
```

That file is ignored by Git. The built-in `pla` and `workspace` roots are not configurable.
Additional roots can be reviewed and changed through the stable Broker capabilities:

```text
core.workspace_roots_get
core.workspace_root_upsert
core.workspace_root_remove
```

Adding, changing, or removing a root requires explicit `INVOKE` confirmation. Mutations use the
current config SHA as an optimistic concurrency precondition. Customer roots are forbidden from
overlapping the PLA source tree. The runtime re-reads the registry when resolving customer roots,
so workspace changes do not require editing PLA source files or restarting the runtime.

## Installer states

- `PASS`: full local installation, reviewed Providers, regression tests, WinGet MCP runtime, and
  customer Secure MCP Tunnel validation all completed.
- `PARTIAL`: safe local installation completed but one optional/full-deployment condition is still
  missing or was explicitly skipped, most commonly customer Tunnel setup or WinGet MCP.
- `BLOCKED`: the installer stops with a terminating error describing a missing prerequisite or
  failed validation.

## Existing installs

Startup accepts Tunnel configuration in this order:

1. `PLA_TUNNEL_CONFIG` when explicitly set;
2. `config/tunnel.local.yaml` when present.

There is no automatic fallback to the tracked `config/tunnel.yaml`. A legacy config may still be used only by explicitly pointing `PLA_TUNNEL_CONFIG` at it. This prevents a fresh clone from ever starting with another deployment Tunnel identity.

## Codex task

The following instruction is suitable for a customer who asks Codex to install PLA:

> Install the requested PLA release on this Windows machine. First audit Git, Python 3.11, Node/npm,
> Microsoft Edge, WinGet MCP, ports 8766/8931/18081, and any existing PLA installation. Clone the
> repository and check out the release tag. Run `install.ps1` rather than manually reimplementing
> the setup. Never reuse another machine Tunnel ID or credential. If the customer Secure MCP Tunnel
> has not yet been created, complete all local installation work and stop at the external authorization
> blocker with status PARTIAL. Once customer Tunnel details are available, rerun `install.ps1` with
> `-TunnelId`, `-TunnelClient`, `-TunnelCredential`, `-PersistEnvironment`, and `-Start`. Do not weaken
> PLA safety controls or commit credentials/local Tunnel configuration. Finish by reporting installer
> status, Provider readiness, pytest result, Tunnel state, Git state, and any remaining blocker.

## Runtime

After installation:

```powershell
.\start_all.ps1
.\stop_all.ps1
.\restart_pla.ps1
```

The Secure MCP Tunnel credential is supplied by file reference at runtime. Secrets must never be
written into tracked repository files.
