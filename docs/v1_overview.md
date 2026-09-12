# PLA architecture overview

## Production decision boundary

PLA uses a ChatGPT-native agent loop:

```text
ChatGPT = reasoning / planning / capability selection / next-step decisions
PLA     = local capability runtime / policy / state / execution / observations
```

PLA intentionally does not embed a second production planner. External providers expose
capabilities; ChatGPT composes them.

## Stable ChatGPT-facing surface

The preferred public surface remains:

```text
capability_search
capability_describe
capability_invoke
```

This prevents every downstream provider or new governance primitive from expanding the
ChatGPT MCP tool schema.

Built-in transaction governance is registered as the internal `core` provider:

```text
core.transaction_create
core.transaction_get
core.transaction_checkpoint
core.transaction_finalize
core.transaction_invoke
```

These capabilities reuse the same durable `TRANSACTION_STORE`, Capability Broker, and
Transaction Action Envelope as the legacy direct transaction MCP tools. They are adapters,
not a second transaction implementation.

## Provider plane

Provider installation and runtime declaration are separate:

- `provider_specs/<id>.txt`: exact dependency pins for isolated Python providers.
- `provider_manifests/<id>.json`: runtime declaration plus capability policy.
- `.provider_envs/<id>/`: ignored isolated Python environments.
- `isolated_python_stdio`: Python provider inside its dedicated environment.
- `executable_stdio`: reviewed native executable provider without a Python wrapper.

The current mainline includes reviewed MarkItDown, DOCX, PDF, Microsoft WinGet,
Windows Management, and Software Migration providers. Provider Doctor checks lifecycle health,
runtime availability, and pinned-version drift.

Tool discovery is dynamic but filtered through manifest allowlists and per-tool overrides.
A downstream tool that appears but is not allowlisted stays hidden.

## Durable local runtime

The local executor retains and extends the v0.8 foundations:

- named workspace roots and protected paths;
- bounded filesystem, search, process, Python, PowerShell, and Git capabilities;
- SQLite persistent Task Store;
- cursor-based incremental observations;
- controlled cancellation;
- SHA-256 optimistic file writes;
- atomic single-file patching;
- transactional multi-file ChangeSet;
- Artifact Plane with immutable hashes and provenance;
- durable Action Transaction Runtime;
- explicit capability confirmation and transaction gates.

The transaction store never chooses capabilities. The action envelope invokes exactly one
caller-selected capability, records target policy and argument/result hashes, and updates one
transaction step. Verification steps require an explicit checkpoint before final commit.

## Interactive elevation plane

Windows UAC interaction is deliberately separated from the background HTTP runtime.

```text
transaction
-> reviewed elevated capability
-> elevation request in ignored runtime state
-> Interactive Elevation Broker in user Session / WinSta0 / Default desktop
-> Windows UAC
-> elevated executable
-> provider-specific verification
-> transaction checkpoint
```

The broker accepts narrowly validated request kinds rather than arbitrary commands. Current
reviewed shapes are a registered application uninstaller and an exact WinGet install command.
For WinGet installation, the broker reconstructs and verifies the full reviewed argv,
including exact package id, source, noninteractive flags, and absolute target directory.

Broker state lives under ignored `state/elevation/`. The broker is started by
`start_all.ps1` and can be managed independently through the dedicated start/stop scripts.

## Software migration E2E

The production migration path is now:

```text
assessment
-> snapshot-backed prepare
-> backup/precondition verification
-> confirmation + transaction gated uninstall
-> UAC through Interactive Elevation Broker
-> confirmation + transaction gated WinGet install
-> install-location verification
-> user-data verification
-> commit / rollback
```

A real Zotero E2E completed the entire sequence. Zotero 10.0.2 was migrated from the default
C-drive program location to `D:\Apps\Zotero`. Registry verification matched the requested
target, the old program directory was absent, and the full user-data manifest plus
`zotero.sqlite` SHA-256 remained identical to the verified pre-migration backup. The durable
transaction was finalized as committed.

## Structured Git boundary

Controlled Git staging now supports explicit modified files and non-ignored new regular files.
The safety properties remain:

- caller supplies every path explicitly;
- caller supplies the current SHA-256 for every staged file;
- expected HEAD must still match;
- Git index must start clean;
- merge/rebase/sequencer states disable structured staging/commit;
- raw bytes are hashed without clean filters;
- staged paths must equal the requested set exactly;
- structured commit uses compare-and-swap branch update;
- no automatic push.

## Security boundary

PLA is a policy-constrained local runtime, not an OS sandbox. Child processes execute with the
host user's authority unless a reviewed capability is explicitly elevated through the
Interactive Elevation Broker.

Important boundaries remain:

- protected local paths and named roots;
- provider and program allowlists;
- no arbitrary shell execution through the public capability surface;
- confirmation gates for privileged/destructive capabilities;
- transaction requirements for reviewed multi-step high-risk actions;
- ignored credentials, runtime state, local environments, logs, and workspaces;
- artifacts keep binary payloads out of model context.

The design goal is capability, auditability, and recoverability without moving planning
authority away from ChatGPT.
