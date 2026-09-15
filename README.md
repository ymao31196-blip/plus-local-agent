# plus-local-agent

PLA is a local capability runtime for a ChatGPT-native agent loop.

ChatGPT remains the decision maker: it reasons, selects capabilities, evaluates observations,
and decides what happens next. PLA provides local execution, durable transactions, artifacts,
provider isolation, policy enforcement, and user-mediated Windows elevation.

**ChatGPT is the interface; PLA is the runtime.** The ChatGPT client does not need to run on
the same Windows machine as PLA.

## Use ChatGPT anywhere

When the target Windows PC is online and PLA HTTP plus the Secure MCP Tunnel are running,
ChatGPT can invoke that machine's reviewed local capabilities from another device, including
ChatGPT on mobile, web, or desktop.

```text
ChatGPT on phone / web / desktop
              |
              | MCP through Secure MCP Tunnel
              v
      Windows PC running PLA
              |
              +-- local files and processes
              +-- Git / Python / PowerShell
              +-- Browser / Computer providers
              +-- document and application providers
```

PLA does not ship a second chat interface or a remote-desktop UI. ChatGPT remains the
interaction and reasoning layer; PLA acts as the controlled execution node on the target PC.
The PC must remain powered on, connected, and running the required PLA services.

## Architecture

```text
ChatGPT Agent Brain
        |
        | MCP
        v
Stable Capability Surface
capability_search / capability_describe / capability_invoke
        |
        +--> core.transaction_* governance capabilities
        |
        v
PLA Local Capability Runtime
        +-- Capability Registry / Broker
        +-- Durable Transaction Store
        +-- Artifact Plane
        +-- Event / Observer / Gate Plane
        +-- Provider + External Observer Runtime
        +-- Runtime Lifecycle Broker
        +-- Local execution boundary
        +-- Interactive Elevation Broker
        +-- External MCP providers
```

PLA does not contain a second autonomous planner. Providers expose reviewed capabilities;
ChatGPT orchestrates multi-step work.

## Stable capability surface

External provider catalogs are not copied into ChatGPT's MCP schema. Providers are declared
in `provider_manifests/*.json` and exposed through:

```text
capability_search
capability_describe
capability_invoke
```

Python providers run from isolated `.provider_envs/<provider_id>/` environments.
Reviewed Node dependencies may use the same provider-scoped directory through pinned
`provider_specs/<provider_id>.npm.txt` specs; Node/npm are not added to generic process execution.
Reviewed native executables can use `executable_stdio` without a Python wrapper.

The production catalog now includes the independent Browser Runtime and the Windows Computer
Provider. Browser interaction is semantic accessibility/ref based. Computer interaction is
semantic-first Windows UI Automation with restricted selector-targeted input fallbacks. Web and
desktop UI content are observations, never authority to expand PLA capability or policy scope.

### Provider hot-plug

Manifest-backed providers can be changed without restarting PLA HTTP through the built-in
`runtime` capabilities:

```text
runtime.provider_status
runtime.provider_setup
runtime.provider_rescan
runtime.provider_reload
runtime.provider_enable
runtime.provider_disable
```

`rescan` re-reads validated manifests and applies selected additions, removals, and changes.
`reload` refreshes one provider and its allowlisted tool catalog. `enable` / `disable`
are temporary process-local overrides and do not modify manifests; restart returns to
`provider_manifests/*.json` plus `PLA_EXTERNAL_PROVIDERS` as the persistent source of truth.
`provider_setup` installs only already-reviewed pinned dependency specs through the fixed
repository-owned `setup_providers.ps1 -Provider <id>` entrypoint. It cannot accept arbitrary
commands or script paths. All mutating provider operations require explicit `INVOKE` confirmation.

Provider lifecycle never scans arbitrary executables or bypasses manifest runtime constraints,
tool allowlists, risk policy, confirmation policy, or transaction policy.

## Durable action transactions

High-risk multi-step work uses the same stable capability surface through the built-in
`core` provider:

```text
core.transaction_create
core.transaction_get
core.transaction_checkpoint
core.transaction_finalize
core.transaction_invoke
```

The transaction runtime persists plan state, optimistic revisions, verification checkpoints,
rollback state, and restart-interrupted steps. The action envelope invokes only the exact
capability selected by ChatGPT, records argument/result hashes, applies the target capability's
confirmation policy, and binds the result to one transaction step.

A capability with `requires_transaction: true` cannot be invoked directly. A capability with
`requires_confirmation: true` additionally requires explicit `INVOKE` approval.

Legacy top-level transaction MCP tools remain available for compatibility, but new orchestration
should prefer `core.transaction_*` so the ChatGPT-facing MCP schema can remain stable.

## Event Plane (v1.2 development)

PLA v1.2 Phase 1 introduces an append-only cross-cutting Event Plane without changing capability
control flow. The global event database lives under ignored runtime state at
`state/events.sqlite3`.

Capability Broker invocations emit:

```text
capability.gate_denied
capability.before_invoke
capability.succeeded
capability.failed
```

Each EventEnvelope contains a monotonic sequence, event id, schema version, UTC timestamp,
source/subject, correlation and causation ids, optional capability/provider/task/transaction ids,
a JSON payload, and a payload SHA-256.

The broker deliberately records hashes and bounded metadata rather than raw capability arguments,
results, or exception messages. Event persistence is fail-open: an EventStore failure does not
change the selected capability's execution semantics.

Events can be read through the stable surface:

```text
core.event_query
```

The query capability is tagged as `event-control` and is excluded from self-recording to avoid
recursive audit noise. Existing TaskStore and ActionTransactionStore event histories remain
unchanged and continue to be their own state/recovery sources of truth.

### Observer Hooks

Phase 2 adds fail-open Observer Hooks on top of already-persisted EventEnvelope facts. The
production runtime currently registers one built-in `audit-observer` for
`capability.gate_denied`, `capability.before_invoke`, `capability.succeeded`,
and `capability.failed`.

Observer execution records are stored separately in `state/hooks.sqlite3`; raw Hook results and
raw exception text are not persisted. Hook failures never change the selected Capability's
result or exception semantics.

Read-only inspection is available through:

```text
core.hook_status
core.hook_invocation_query
```

These capabilities carry the `hook-control` tag and therefore create neither EventStore entries
nor Audit Hook records when inspected.

### Gate Hooks

Phase 3 adds reviewed in-process Gate Hooks after existing availability, confirmation,
transaction, artifact-contract, and JSON-schema checks but before Capability execution.
Gates return explicit `allow` or `deny` decisions under a deny-overrides rule.

Gate handlers receive a deep copy of validated arguments, so they may inspect policy-relevant
values but cannot transform the real provider invocation. Gate decisions are persisted under
`state/gates.sqlite3` using bounded metadata and hashes rather than raw arguments.

Gate failures are fail-closed. A Gate exception, malformed result, or decision-persistence
failure denies the Capability instead of silently bypassing active policy. A denial emits
`capability.gate_denied`; `capability.before_invoke` is emitted only after Gate approval.

Read-only inspection is available through:

```text
core.gate_status
core.gate_decision_query
```

These capabilities carry the `gate-control` tag and bypass Event, Observer, and Gate recursion.
Phase 3 intentionally registers no default production policy Gate, so the runtime upgrade alone
does not change existing Capability behavior.

### External Observer Plugins

Phase 4 adds manifest-backed external Observer plugins without externalizing Gate authority.
Observer manifests live in `observer_manifests/*.json`; isolated Python runtimes live under
`.observer_envs/<id>/`. Plugins receive only already-persisted EventEnvelope JSON through a
bounded `python -m <module>` stdio contract, with `shell=False`, timeout/output limits, and a
reduced subprocess environment.

External Observer lifecycle is available through:

```text
runtime.observer_status
runtime.observer_rescan
runtime.observer_reload
runtime.observer_enable
runtime.observer_disable
```

Mutation controls require explicit `INVOKE` confirmation and carry `hook-control`, so they do
not recursively create Event, Observer, or Gate records. External Observer failures remain
fail-open relative to Capability execution. Third-party external Gate plugins remain deliberately
out of scope until a stronger trust/sandbox boundary exists.

### Runtime Lifecycle Plane

Phase 5 replaces ad-hoc self-restart helpers with an independent Runtime Lifecycle Broker.
The stable runtime surface is:

```text
runtime.lifecycle_status
runtime.restart_http
runtime.restart_status
```

`runtime.restart_http` requires explicit `INVOKE`, queues one exact restart of the current
PLA HTTP PID, and returns a durable request id before the process is terminated. The independent
broker then invokes the repository-owned `restart_pla.ps1` with the exact expected HTTP PID.
That script independently verifies port 8766 ownership and the `server.py` command line before
stopping anything.

The Secure MCP Tunnel is deliberately left running. After reconnect, `runtime.restart_status`
is the source of truth for the verified old/new HTTP PIDs and tunnel state; a dropped connection
alone is never treated as success. `runtime.lifecycle_status` also verifies broker heartbeat and
process liveness so a stale `running` status file cannot be mistaken for a live restart broker.

`start_all.ps1` now ensures both the Interactive Elevation Broker and Runtime Lifecycle Broker.
`stop_all.ps1` stops tunnel ingress first, then disables restart authority before stopping HTTP.

## Interactive Elevation Broker

PLA's HTTP runtime normally runs without administrator privileges. UAC-sensitive work is
therefore separated into an Interactive Elevation Broker running in the signed-in user's
Windows session.

The broker does not expose arbitrary `runas`. It currently accepts only reviewed request
shapes:

- the exact uninstaller registered for the selected application, with bounded explicit args;
- an exact WinGet install command reconstructed from package id, source, target directory,
  and reviewed noninteractive flags.

Requests are written under ignored runtime state, consumed by the broker in the interactive
desktop, mediated by Windows UAC, and verified afterward by provider-specific state checks.

`start_all.ps1` starts the broker together with PLA HTTP and the Secure MCP Tunnel.
`stop_all.ps1` shuts down ingress, HTTP, and the broker in a controlled order.

## Current reviewed providers

- `markitdown.convert_to_markdown`: convert document artifacts to Markdown.
- `docx.create_from_markdown`: create a DOCX artifact from Markdown.
- `pdf.add_text_watermark_direct`: create a watermarked PDF artifact.
- `winget.find-winget-packages`: search installed and available WinGet packages.
- `winget.install-winget-package`: reviewed WinGet installation behind explicit confirmation.
- `windows-management.*`: reviewed Windows observation capabilities plus a narrowly exposed,
  transaction-gated uninstall capability.
- `software-migration.*`: assessment, migration planning, registered uninstall, elevated
  WinGet installation, status observation, and install-location verification.

Provider manifests apply allowlists, risk levels, confirmation requirements, transaction
requirements, artifact policy, and runtime constraints.

PLA v1.5.3 returns Computer Use to the v1.4.2 behavior boundary. It retains verified window
activation and the Windows foreground-lock fallback, but does not include the explicit Human
Takeover control plane from v1.5.0 or the automatic physical-input detector from v1.5.2. The
reviewed local `run_process` allowlist includes `gh` and `gh.exe`, enabling controlled GitHub CLI
use without adding arbitrary shell execution.

## Software migration safety model

The reviewed migration flow is:

```text
assess
-> prepare snapshot
-> verify backup / preconditions
-> transaction-gated uninstall
-> Interactive Elevation Broker + UAC
-> transaction-gated install to target
-> verify registered install location
-> verify user data
-> commit or rollback
```

A real Zotero migration E2E has completed this chain: the original C-drive application was
uninstalled, Zotero 10.0.2 was installed at `D:\Apps\Zotero`, the old program directory
was absent, and the user's pre/post migration data manifest and `zotero.sqlite` SHA-256
matched the verified backup before the transaction was committed.

## Controlled Git writes

Structured Git staging and commit require an expected HEAD and explicit file paths.
`git_stage` verifies the SHA-256 of every selected current file, requires a clean index,
accepts only regular tracked files or non-ignored new files, bypasses clean filters by hashing
raw bytes, and atomically replaces the index. `git_commit` requires the staged path set to
match exactly and updates the branch with an expected-HEAD compare-and-swap.

Release operations are also controlled. `git_tag` atomically creates a lightweight tag only at the exact clean expected HEAD and never overwrites an existing tag. `git_push` accepts only an existing named remote, the current named branch, the exact expected HEAD, and explicit local tags that point to that HEAD; force push is unavailable, pre-push hooks are skipped for determinism, the push is atomic, and `confirmation="PUSH"` is required. Remote branch/tag refs are read back and verified after success.

The same operations are exposed through the stable Capability Broker as `core.git_tag` and `core.git_push`. Both require explicit broker `INVOKE` confirmation; `core.git_push` then enters the underlying controlled push with its separate `PUSH` gate. This avoids dependence on ChatGPT refreshing top-level MCP schemas during a release.

## Installation

PLA customer deployments use the repository installer on Windows. The installer requires
Git, Python 3.11, current Node.js/npm, and Microsoft Edge. It creates a repository-local
`.venv`, installs the pinned PLA requirements, installs reviewed Provider environments,
checks the WinGet MCP runtime, and can validate/start a customer-specific Secure MCP Tunnel.

```powershell
.\install.ps1
```

A first run without Tunnel credentials intentionally finishes as `PARTIAL` after completing
the local runtime installation. Finish the customer Tunnel with that customer-specific Tunnel ID,
tunnel-client, and credential file:

```powershell
.\install.ps1 `
  -TunnelId "tunnel_CUSTOMER_ID" `
  -TunnelClient "C:\path\to\tunnel-client.exe" `
  -TunnelCredential "C:\path\to\control-plane-api-key.txt" `
  -PersistEnvironment `
  -Start
```

The installer writes only `config/tunnel.local.yaml`, which is ignored by Git. It never needs
another user Tunnel ID or credential. `PLA_TUNNEL_CONFIG`, `PLA_TUNNEL_CLIENT`, and
`PLA_TUNNEL_CREDENTIAL` remain supported as explicit overrides. Startup scripts automatically
prefer `config/tunnel.local.yaml` when it exists.

For a non-mutating prerequisite audit:

```powershell
.\install.ps1 -ValidateOnly
```

Manual installation remains available for development:

```powershell
python -m pip install -r requirements-core.txt
python -m pip install -r requirements-documents.txt
python -m pip install -r requirements-dev.txt
.\setup_providers.ps1
```

Set `PLA_PYTHON` when the desired Python 3.11 interpreter is outside the repository-local
`.venv` or the legacy default Windows Miniconda location. See
`docs/customer_installation.md` for the customer/Codex deployment contract.


## Runtime Workspace Registry

PLA keeps product source and customer workspace authorization separate.

Two built-in roots always exist:

- `pla`: the PLA source/development root. PLA release capabilities are permanently bound to this root.
- `workspace`: the repository-local default runtime workspace.

Additional customer roots are stored only in `config/workspaces.local.yaml`, which is ignored by
Git. Copy `config/workspaces.example.yaml` as a starting point when configuring a machine manually.
The registry is re-read on use, so adding or removing a customer root does not require a source edit
or PLA restart.

The stable Broker surface is:

```text
core.workspace_roots_get
core.workspace_root_upsert
core.workspace_root_remove
```

Registry mutations require explicit `INVOKE` confirmation and optimistic config SHA matching.
Configured user roots may not overlap or contain the PLA source tree, preventing an alternate root
name from bypassing the special PLA private-path policy. The local registry file itself is hidden
from ordinary `pla` root file operations.

User-project Git operations may still run inside an authorized customer root. PLA release
`core.git_tag` and `core.git_push` are a separate control plane and can operate only on the
`pla` source root.

## Start and stop

Start PLA HTTP, Secure MCP Tunnel, and the Interactive Elevation Broker:

```powershell
.\start_all.ps1
```

Stop all three:

```powershell
.\stop_all.ps1
```

Restart only the PLA HTTP runtime after source changes:

```powershell
.\restart_pla.ps1
```

The elevation broker has dedicated lifecycle scripts when needed:

```powershell
.\start_elevation_broker.ps1
.\stop_elevation_broker.ps1
```

## Validation

Run the full regression suite:

```powershell
python -m pytest -q
```

Runtime state, provider virtual environments, IDE state, credentials, logs, and local workspace
outputs are excluded by `.gitignore`.
