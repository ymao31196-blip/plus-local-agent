# PLA v2.0.0

PLA v2.0.0 promotes the local execution architecture from an HTTP-process-bound runtime to a split **Control Plane / Execution Plane** model. ChatGPT remains the Agent Brain; PLA keeps capability routing, semantic policy, task state and confirmation in the Control Plane, while default generic one-shot processes execute through an authenticated out-of-process Windows Execution Runner.

This is a major release because the default process ownership and restart model changes while preserving PLA's existing workspace, Git, confirmation, transaction and Provider boundaries.

## Production Execution Runner

Generic `run_process` now resolves to the production Execution Runner by default.

The Runner:

- runs as an independent Windows process;
- communicates with PLA through an authenticated Named Pipe protocol;
- records a stable Runner instance identity, PID, Windows process creation time and executable image path;
- uses a PLA-root-scoped Windows mutex to prevent multiple Runner instances from claiming the same runtime;
- revalidates workspace/root execution policy, program allowlist, protected environment overrides and command semantics before spawning a child;
- retains bounded execution results by exact `execution_request_id`;
- exposes no arbitrary PID-kill surface.

`backend="in_process"` remains available as a diagnostic/emergency compatibility path. Persistent process sessions and structured PowerShell retain their specialized runtimes and are not migrated in this release.

## Restart-safe task ownership

Every production one-shot request receives an exact 32-hex `execution_request_id`.

After Runner acceptance, PLA never blindly replays potentially mutating work on another backend. If the HTTP Control Plane is replaced while a production task is still running, the new TaskStore owner can reattach to the retained Runner ledger using the original request ID and complete the original task record without resubmitting the command.

The recovery path is intentionally narrow. Only a persisted running single-action production `run_process` task with an exact accepted request ID is eligible. Other interrupted tasks keep the previous conservative no-replay behavior.

## Request-ID cancellation

Runner protocol v4 adds request-ID-scoped cancellation.

The Runner stores only the child process handle it owns for a submitted request. `cancel_task(task_id)` can forward the exact production `execution_request_id`; the Runner terminates that owned child tree and records a cancelled result. Callers never provide a PID to this interface.

## Semantic execution governance

Command semantics are classified before generic execution. Policy provenance is recorded with stable `policy_id` / `policy_version` metadata.

Governed routes include:

- external writes -> `runtime.external_process` + explicit `INVOKE`;
- Python environment mutations such as pip install/uninstall -> `runtime.environment_process` + explicit `INVOKE`;
- high-risk local Git operations -> `runtime.local_mutation_process` + explicit `INVOKE`;
- specialized PLA-source Git operations -> `core.git_*` capability surface.

The production Runner rechecks the applicable low-level policy and cannot be used as a route around Capability Steering.

## Persistent process sessions and ConPTY

v2.0.0 adds the persistent session runtime introduced during the Runtime parity work:

- pipe sessions;
- native Windows ConPTY sessions;
- bounded incremental output reads;
- stdin writes and EOF;
- resize / terminate / close;
- stable `session_id` and `execution_id`;
- monotonic operation sequence;
- process-tree ownership.

Interactive Python code input is governed separately. Unconfirmed executable REPL input is blocked; `runtime.confirmed_session_write` requires explicit `INVOKE` and re-evaluates the live session policy before writing.

Persistent sessions intentionally remain on their specialized runtime instead of being forced through the production one-shot Runner.

## Execution observability

The runtime now records a connected trace across:

- task ID;
- capability correlation / causation IDs;
- execution request ID;
- Runner identity;
- session / execution IDs where applicable;
- operation sequence.

Execution permission metadata records the selected root, route, program policy, semantic classification, confirmation state and the actual sandbox boundary.

PLA explicitly reports `sandbox_mode=none`. Workspace/root checks are **wrapper policy**, not an OS filesystem/process sandbox.

## Shadow and canary validation path

Before default cutover, PLA introduced explicit candidate, shadow and canary backend modes.

Shadow execution was restricted to deterministic local read-only Git structure queries and recorded comparison telemetry using return codes, lengths and SHA-256 values rather than duplicating raw shadow output. Canary routing proved candidate-first execution with fallback only for explicitly safe repeatable reads.

These compatibility/diagnostic backend names remain available after v2.0.0, but the production default is now the independent Runner.

## Office补强 and Provider surface

v2.0.0 also includes the Office补强 (`office-enhancement`) Provider:

- structured `@oai/artifact-tool` editable PowerPoint overlays;
- `zipfile + lxml` OOXML surgical merge;
- Microsoft PowerPoint COM rendering and text-overflow inspection;
- PyMuPDF PDF image extraction and page/region rendering.

The live v2.0.0 environment has **10 active external Providers**. WPS Office exposes 250 capabilities and Skill Library exposes four.

The stable direct MCP surface remains **23 top-level tools**; specialized operations continue to be discovered through the Capability Registry. At the live release validation point, the Registry contains **412 dynamic capabilities**.

## Documentation

- `README.md` is the English main documentation.
- `README.zh-CN.md` is a complete Simplified Chinese counterpart, including installation, architecture, governance, Provider, Runtime lifecycle and validation sections.
- `docs/codex_runtime_parity_audit.md` records the Runtime evolution from the unified execution seam through production Runner cutover.

## Validation

Automated validation before release:

- Phase-S focused production Runner / recovery / cancellation suite: **65 / 65 passed**.
- Final full repository regression: **762 / 762 passed** in 117.71 s.
- Live Capability Registry: **412 dynamic capabilities**.
- Active external Providers: **10**.
- WPS Office Provider: **250 capabilities**, ready.
- Skill Library Provider: **4 capabilities**, ready.

Live ChatGPT -> Secure MCP Tunnel -> PLA graduation E2E verified:

1. Default `run_process` resolved to the production Runner rather than the HTTP process.
2. Production Runner protocol v4 ran as an independent process and spawned a separate child.
3. A running task survived HTTP replacement; the new Control Plane recovered the **same task ID** using the **same execution request ID** without replaying the command.
4. `cancel_task` forwarded the exact request ID, terminated the Runner-owned child tree and produced a retained `cancelled` Runner result.
5. `pip install` remained routed to the confirmation-gated environment capability.
6. `git push` against PLA source remained routed to the governed Git capability surface.

## Security boundary and non-goals

v2.0.0 does **not** claim a general OS sandbox. The current generic filesystem boundary remains a PLA wrapper policy, and allow-listed interpreters can have capabilities that exceed path arguments supplied through PLA unless the operating system separately constrains them.

This release also does not migrate persistent ConPTY sessions into the out-of-process one-shot Runner, add multi-Runner scheduling, containers, remote execution workers or a second autonomous planning model. Those remain future work only if real usage justifies them.
