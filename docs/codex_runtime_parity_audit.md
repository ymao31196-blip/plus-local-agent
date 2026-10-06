# Codex Runtime → PLA parity audit

Date: 2026-09-30  
Last updated: 2026-10-06

## Scope

This audit treats the locally running Codex process as a behavioral reference and
the public OpenAI Codex runtime implementation as the design reference.  The goal
is not to embed or depend on Codex.  The goal is to evolve PLA into a stronger
ChatGPT-native local execution runtime while preserving PLA's Windows-first
workspace, transaction, capability-routing, artifact, and MCP boundaries.

Observed locally:

- A `codex` process is running.
- The PLA service environment does not currently resolve `codex` through
  `Get-Command`; `run_process(program="codex")` is also rejected by the
  current program allowlist.
- Therefore local black-box parity tests must either use an explicitly registered
  Codex executable path/capability later, or remain host-observation-only.
- PLA root is clean at the start of this audit.

Public Codex reference points checked against current `openai/codex`:

- `codex-rs/core/src/unified_exec/mod.rs`: unified interactive execution,
  process reuse, bounded output, cancellation, approval/sandbox orchestration.
- `codex-rs/core/src/tools/approvals.rs`: centralized approval stage.
- `codex-rs/core/src/tools/sandboxing.rs`: shared sandbox orchestration.
- `codex-rs/features/src/lib.rs`: UnifiedExec, UnifiedExecTty,
  ExecPermissionApprovals, WriteStdinApproval.
- approval-policy templates: additional filesystem/network permissions are
  preferred over fully unsandboxed escalation.

## Current PLA baseline

| Runtime concern | PLA state before this work | Assessment |
| --- | --- | --- |
| One-shot process execution | `run_process` + structured PowerShell | strong |
| Program allowlist | explicit executable allowlist | strong but coarse |
| Workspace path policy | `safe_path` and per-root read/write/execute ACL | strong wrapper policy |
| Process-tree cancellation | Windows `taskkill /T /F` on runtime-owned child | strong |
| Bounded stdout/stderr | 20k tail + original length/truncation metadata | strong |
| Durable task records | SQLite/WAL TaskStore + cursor events | strong |
| Restart recovery | running/queued tasks fail closed; no replay | explicit limitation |
| Capability routing | generic→specialized steering | strong |
| Confirmation | per-capability `INVOKE`, Git `PUSH`, transactions | present but distributed |
| Elevation | dedicated interactive elevation broker | strong Windows-specific capability |
| Patch concurrency | expected SHA-256 + transactional multi-file changeset | strong |
| PTY / ConPTY | absent | gap |
| Persistent terminal session | absent | gap |
| write_stdin to live process | absent | gap |
| OS-level filesystem/process sandbox for generic executables | no implementation found | critical gap |
| Per-exec additional permissions | absent | gap |
| Central exec approval envelope | partial/distributed across subsystems | gap |
| Sandbox-denial retry/escalation | absent | gap |
| Live process output events | final-output events only before Phase A | gap |

## Security finding

`safe_path` constrains paths supplied to PLA tools and constrains the working
directory chosen for `run_process`.  It does not by itself sandbox the child
process.  Because general interpreters such as Python are allow-listed, an
allow-listed child may be capable of accessing resources outside the selected
PLA root unless the operating system separately restricts it.

Therefore PLA should describe the current boundary as a controlled execution
policy, not a complete filesystem sandbox.  PTY/session work should not widen
this boundary before an OS-level sandbox/permission profile exists.

## Upgrade order

1. **Phase A — Unified one-shot runtime — completed**
   - child launch is centralized in a resolved `ExecutionRequest`;
   - generic process and structured PowerShell now share `run_oneshot`;
   - background tasks emit bounded line/chunk `process_output` observations;
   - all existing public tool schemas and guards are preserved;
   - verification: targeted runtime/foundation suite 115 passed; full suite 662 passed;
   - Tunnel E2E: first output line was observed while the task remained `running`,
     several seconds before the second line and terminal task state.

2. **Phase B — Execution permission envelope — completed for local process runtime**
   - each one-shot launch carries an `ExecutionPermissionEnvelope` with selected
     root, cwd, route, program policy, confirmation/transaction state and current
     filesystem/network/elevation boundary;
   - the envelope deliberately reports `filesystem_boundary=wrapper_policy`
     and `sandbox_mode=none` until an OS-enforced sandbox exists;
   - `execution_started`, `execution_completed`, and `execution_failed`
     observations expose the same envelope to durable task events;
   - existing Capability Broker risk/confirmation/transaction policy remains
     authoritative and is not duplicated or weakened;
   - Tunnel E2E verified the envelope while the task was still `running`;
   - verification: targeted runtime/foundation suite 115 passed; full suite 662 passed.

3. **Phase C — Windows sandbox backend — deferred by owner**
   - no executable access is broadened while this phase is deferred;
   - later evaluate restricted tokens/AppContainer/Job Object/ACL staging or
     another Windows-native strategy;
   - continue to distinguish wrapper path policy from enforced child-process
     permissions.

4. **Phase D — persistent session runtime + native ConPTY — completed**
   - `runtime.process_session` provides open/write/read/resize/close over opaque
     PLA-owned session ids with incremental cursors and bounded event history;
   - the stable default remains `terminal_mode=pipe`; native Windows ConPTY is
     available explicitly through `terminal_mode=conpty` without adding a third-party
     PTY dependency;
   - ConPTY sessions use the same program allowlist, root/cwd validation and
     `ExecutionPermissionEnvelope`, reporting route `interactive_conpty_session`
     and truthfully retaining `sandbox_mode=none`;
   - Windows Job Object containment uses kill-on-close so a session cannot detach
     a surviving descendant process tree;
   - ConPTY output is preserved as the real UTF-8 + VT sequence stream; input is
     raw terminal input and resize uses `ResizePseudoConsole`;
   - a Windows standard-handle duplication issue caused by PLA's redirected host
     stdout was fixed with explicit `STARTF_USESTDHANDLES`, keeping child output
     inside the pseudoconsole channel;
   - verification: ConPTY targeted session suite passed, full suite 672 passed,
     and live ChatGPT → Tunnel → PLA E2E verified READY, stdin echo, resize and close;
   - elevated interactive sessions are not exposed, so write-to-elevated-session
     approval is currently out of scope rather than silently bypassed.

5. **Phase E — local Codex parity harness — metadata parity completed**
   - `runtime_parity.py` now runs repeatable PLA probes for one-shot stdout/stderr,
     timeout handling, persistent pipe sessions and persistent ConPTY sessions;
   - the harness reports structured pass/fail/blocked outcomes and preserves the
     same execution-permission metadata used by the runtime itself;
   - Codex discovery accepts an explicit path, `CODEX_EXECUTABLE`, PATH-resolved
     `codex`/`codex.exe`, or the currently installed Windows Appx package resolved
     through the AppModel registry and `PackageRootFolder`;
   - the current package is `OpenAI.Codex_26.930.2377.0_x64__2p2nqsd0c76g0`, with
     runtime CLI at `app/resources/codex.exe`; metadata probe reports
     `codex-cli 0.159.0-alpha.12.1`;
   - the same package also contains `codex-code-mode-host.exe`,
     `codex-command-runner.exe`, `codex-windows-sandbox-service.exe` and
     `codex-windows-sandbox-setup.exe`, confirming separate local runtime helpers;
   - local result: PLA 4/4 probes pass and Codex metadata probe passes without
     adding Codex to PLA's generic executable allowlist;
   - final repository verification after Appx auto-discovery integration:
     674 tests passed;
   - task-level Codex black-box probes remain intentionally limited until each
     invocation can be bounded as a dedicated parity action rather than exposed
     through the generic process surface.

6. **Phase F — process protocol hardening — completed**
   - persistent sessions now distinguish `close_stdin`, `terminate` and `close`:
     closing stdin only delivers EOF, terminate ends the owned process tree while
     retaining the readable session record, and close performs final resource release;
   - `write` remains backward-compatible for UTF-8 text and also accepts bounded
     `input_base64` for raw byte delivery without forcing terminal data through text;
   - state-changing interactions are serialized by a per-session interaction lock,
     while long-poll reads stay outside that lock so read/write cannot deadlock;
   - every state-changing interaction advances a monotonic `operation_seq`, and each
     session now has a stable `execution_id` propagated through session events and
     read results; the final code also returns that id directly from state-changing actions;
   - session reads accept `max_output_bytes` and report original/returned/omitted
     byte counts plus explicit truncation metadata; callers can re-read the same
     cursor with a larger budget because the retained event history is not mutated;
   - live ChatGPT → Tunnel E2E verified raw bytes `00 41 0D 0A`, EOF delivery,
     persistent `execution_id`, monotonic operation sequence, recoverable output
     truncation (`5002 → 100`, then full recovery), terminate and post-terminate reads;
   - no program allowlist, workspace root, Git, elevation or sandbox policy was
     broadened by this phase; `sandbox_mode=none` remains explicit;
   - verification: session-focused suite 16 passed; full repository suite 680 passed;
   - final HTTP reload completed and live E2E confirmed `open → write → terminate → close`
     returns one stable `execution_id` directly on every state-changing action, with
     monotonic `operation_seq` values `0 → 1 → 2 → 3`.

7. **Phase G — command semantic policy — completed**
   - `command_semantics.py` adds deterministic action classification above executable
     allowlisting, reporting domain, resolved action, risk level, effect class,
     network intent, confidence and bounded explanatory reasons;
   - the initial classifier covers Git, GitHub CLI, Python, pytest, LaTeX builders and
     WSL, including Git read/local-write/external-write distinctions, `gh` external
     reads/writes, `python -m pip/ensurepip/venv` environment mutation and arbitrary
     Python/test execution;
   - command semantics now flow into `ExecutionPermissionEnvelope`, direct
     `run_process` results, persistent-session permissions and
     `core.capability_route` preflight results;
   - Git governance is action-aware: PLA-root Git remains specialized-enforced for
     all actions, while non-PLA roots only enforce `add`, `rm`, `commit`, `tag` and
     `push` because those actions already have governed capability replacements;
   - read-only workspace Git such as `status` remains generic-allowed, and currently
     unsupported mutations such as checkout/rebase are classified but not blocked
     merely for the sake of parity;
   - GitHub CLI external writes are classified as `write_external`; Phase H adds the
     confirmation-gated governed execution surface for these generic external writes;
   - no executable was added to the allowlist and no workspace, elevation or sandbox
     boundary was widened; `sandbox_mode=none` remains explicit;
   - verification: semantic/steering/runtime/session focused tests pass, and the full
     repository suite passes with 694 tests.

8. **Phase H — semantic external-write approval — completed**
   - new `runtime.external_process` is a Runtime Capability with
     `risk_level=write_external` and `requires_confirmation=True`;
   - generic process requests classified as `write_external` are specialized-enforced
     to this confirmation-gated surface unless a more specific governed capability
     wins first; `git push` therefore remains on `core.git_push`;
   - the handler preserves the existing program allowlist, root/safe-path, timeout,
     environment and execution-observation policy, bypassing only generic steering
     after Broker confirmation has already succeeded;
   - the handler independently reclassifies the command and rejects anything that is
     not `write_external`, preventing the confirmation capability from becoming a
     generic execution bypass;
   - its `ExecutionPermissionEnvelope` truthfully records
     `confirmation_required=True` and `confirmation_supplied=True` together with the
     resolved command semantics;
   - unit coverage verifies that missing `INVOKE` is rejected before the handler is
     entered, confirmed calls can enter the mocked handler, non-external commands are
     rejected, and external semantics/confirmation metadata survive into execution;
   - routing catalog now includes the semantic external-write rule, while no executable,
     workspace, elevation or sandbox boundary was widened;
   - verification: focused governance suite 46 passed; full repository suite 700 passed.

    - live HTTP reload verified `gh issue create` routes to `runtime.external_process`,
      while invoking that capability without `INVOKE` is rejected by Capability Broker
      before handler/schema execution; no external GitHub write was performed;
    - direct generic `run_process` E2E with an unknown `gh` action was conservatively
      blocked as `write_external` and returned the confirmation-gated capability instead
      of starting the process; `git push` still resolves to `core.git_push`.

9. **Phase I — high-risk local mutation governance — completed**
   - new `runtime.environment_process` and `runtime.local_mutation_process` capabilities
     both use `risk_level=write_local` with `requires_confirmation=True`;
   - environment governance is deliberately narrow: `python -m pip install/uninstall`,
     `ensurepip`, and `venv` are confirmation-gated, while read-only pip queries such as
     `list`, `show`, `freeze`, `check`, and package-index inspection remain read routes;
   - high-risk workspace Git actions `checkout`, `switch`, `reset`, `clean`, `rebase`,
     `merge`, `cherry-pick`, and `revert` route to `runtime.local_mutation_process`;
   - existing specialized Git capabilities retain priority for `add`, `rm`, `commit`,
     `tag`, and `push`, preserving Git-specific HEAD/SHA/staging/release constraints;
   - both confirmed local-process handlers independently reclassify the command before
     execution, preventing either confirmation surface from becoming a generic process
     bypass; their permission envelopes record confirmation and semantic metadata;
   - no executable was added to the allowlist and no workspace, elevation, network, or
     sandbox boundary was widened; `sandbox_mode=none` remains explicit;
   - verification: focused semantic/steering/capability suite 56 passed; full repository
     suite 710 passed;
   - live HTTP reload verified both Runtime capabilities are registered with
     `requires_confirmation=True`; `pip list/show` remain generic read routes,
     `pip install` routes to `runtime.environment_process`, and `git reset` routes to
     `runtime.local_mutation_process` without executing either mutation;
   - invoking either confirmation-gated capability without `INVOKE` is rejected by
     Capability Broker before handler execution; live routing audit reports all six
     executable/catalog rules healthy with zero missing capabilities.

10. **Phase J — interactive session input governance — completed**
   - Python execution semantics now distinguish `repl`, `interactive`, `inline_code`,
     `script`, and `module.code`, allowing stdin policy to inherit whether input is code
     or ordinary data instead of treating every Python session identically;
   - persistent-session writes now evaluate an input policy before bytes reach stdin;
     interactive Python code input is blocked with `session_input_confirmation_required`,
     while inline/script stdin remains ordinary data and Ctrl+C remains direct control input;
   - new `runtime.confirmed_session_write` uses `risk_level=write_local` with
     `requires_confirmation=True`; its handler re-evaluates the live session policy and
     rejects use against sessions whose input does not require confirmation;
   - blocked writes do not advance `operation_seq` and never enter the child process;
     confirmed writes record `write_confirmed`, confirmation metadata and the stable
     `execution_id` without exposing submitted input in policy metadata;
   - verification: focused session/semantic suite 34 passed; full repository suite
     716 passed;
   - live ChatGPT → Tunnel → ConPTY E2E opened a Python REPL, blocked an unconfirmed
     executable input at `operation_seq=0`, then accepted the same session through
     `runtime.confirmed_session_write + INVOKE` and returned `PHASE_J_CONFIRMED_OK`;
     the blocked input was never observed or executed.

11. **Phase K — unified semantic execution policy — completed**
   - confirmation-oriented command governance is centralized in
     `semantic_execution_policy.py` instead of duplicating external-write,
     environment-change and high-risk Git predicates across Runtime layers;
   - stable policy provenance (`policy_id`, `policy_version`) is returned by steering,
     revalidated by confirmed Runtime handlers, and recorded in
     `ExecutionPermissionEnvelope` for both allowed and confirmation-gated execution;
   - existing routing rule IDs, priorities, Git specialization and confirmation behavior
     remain unchanged; the policy layer is a single source of truth rather than a new
     permission surface;
   - verification: focused policy/steering/runtime suite 65 passed; full repository suite
     720 passed;
   - live E2E verified `pip install` routes with `execution_policy=environment_change v1`,
     while an allowed generic Python Task records `execution_policy_id=generic_allowed`
     and `execution_policy_version=1` in both execution-started and completed events.

12. **Phase L — runtime trace correlation — completed**
   - `ExecutionContext` now carries Task identity and Capability Broker exposes invocation
     trace through a context-local chain containing correlation, causation, capability and
     transaction identifiers;
   - one-shot execution events/results expose the active trace without adding trace fields
     to business arguments;
   - persistent sessions retain an `origin_trace`, while each state-changing interaction
     records the current interaction trace alongside the stable `session_id`, `execution_id`
     and monotonic `operation_seq`;
   - verification: focused trace/broker/session suite 56 passed; full repository suite
     723 passed;
   - live E2E verified Task execution events carry their actual `task_id`; a persistent
     session preserved one origin correlation ID while a later write recorded a distinct
     interaction correlation ID under the same session/execution identity.

13. **Phase M — execution backend seam — completed**
   - process creation is centralized behind `execution_backend.py`; one-shot execution and
     persistent pipe/ConPTY sessions now consume the same backend identity and launch seam;
   - Runtime results/session records expose stable runner metadata including
     `runner_instance_id`, protocol version and process-isolation truth;
   - the current default is explicitly `backend=in_process_windows` with
     `process_isolation=shared_http_process`; this phase does not claim process isolation
     that does not yet exist;
   - verification: focused backend/runtime/session suite 36 passed; full repository suite
     726 passed;
   - live E2E verified one one-shot Task and one persistent session reported the same
     runner instance from the reloaded HTTP process.

14. **Phase N — out-of-process Execution Runner candidate — completed**
   - a detached Windows Named Pipe runner candidate now has durable state/auth,
     authenticated ping/shutdown, independent PID and explicit candidate lifecycle
     capabilities; it is not the default backend;
   - the transport spike was deliberately narrowed to `ping / probe / shutdown` only.
     The caller cannot provide a command, cwd, env or stdin; the fixed probe is selected by
     the runner itself and returns both Runner PID and child PID to demonstrate process
     separation without creating an arbitrary execution bypass;
   - `runtime.execution_runner_status` and `runtime.execution_runner_probe` are read-only;
     start/stop remain privileged and require explicit `INVOKE`;
   - detached-launch tests verify the Runner outlives its launcher, fixed probe execution
     occurs in a distinct child PID, and the removed legacy `run_oneshot` operation is
     rejected;
   - verification: focused runner/lifecycle/release-freeze suite 7 passed; full repository
     suite 730 passed;
   - live ChatGPT → Tunnel E2E started runner instance
     `f7b259f8d2bd49a9b95513a14d5ab2c8` at PID 6624, then completed a fixed probe in child
     PID 27816;
   - the HTTP Control Plane was restarted from PID 21892 to PID 16936 while the candidate
     Runner remained reachable with the same runner instance id and PID 6624;
   - a second fixed probe after the HTTP restart completed in child PID 29288 through the
     same Runner, proving the detached Execution Runner survives HTTP-process replacement;
   - the authorized stop then completed successfully and the final Runner status returned
     `state=stopped`, `running=false`, `reachable=false`.

15. **Phase O — opt-in out-of-process one-shot backend — completed**
   - `ExecutionRequest` and `run_process` now support explicit `backend=default|candidate_runner` selection; the default remains the stable in-process backend and persistent sessions are unchanged;
   - the Runner protocol advances to v2 with `run_resolved`, but receives only a selected root name plus relative cwd rather than an arbitrary absolute working directory;
   - the independent Runner re-applies the shared executable allowlist, workspace/root execute policy, env-override validation and semantic policy before spawning a child;
   - commands requiring external-write/environment/high-risk-local confirmation, Git actions with specialized capabilities, and any Git command against the PLA source root are independently rejected by the Runner even if sent directly over its authenticated pipe;
   - program and environment policy were moved into shared low-level definitions so the Control Plane and Runner consume the same allowlist and protected environment-variable rules;
   - semantic steering still executes before backend selection, so choosing `candidate_runner` cannot bypass `core.git_push`, environment-change approval, external-write approval or other specialized routing;
   - execution events record both requested backend preference and actual Runner metadata; direct `run_process` results report the actual Runner that handled the request;
   - focused runner/backend/runtime/steering/MCP-wrapper verification: 55 passed; full repository suite: 736 passed;
   - targeted tests also verify env propagation, root-escape rejection, Runner-side semantic rejection and fail-closed behavior when no candidate Runner is active;
   - the final live HTTP reload completed from PID 31168 to PID 23488. The current chat tool schema remained connection-cached, but TaskStore execution on the reloaded Runtime accepted the new `backend=candidate_runner` argument and failed closed with `candidate execution runner is not running` while the candidate was stopped;
   - a control Task on the same PID 23488 Runtime executed `PHASE_O_DEFAULT_OK` with `backend_preference=default` and reported `backend=in_process_windows`, confirming the default path remained unchanged and there is no implicit fallback from an unavailable candidate Runner;
   - the owner then authorized the privileged live success path. Runner instance `0728bfd0fb1549ffa5896760e0e52a66` started at PID 31440 with protocol v2 and `scope=opt_in_text_oneshot`; a TaskStore `run_process` using `backend=candidate_runner` executed `PHASE_O_CANDIDATE_OK` in child PID 4140 and reported the same independent Runner identity, while a parallel `backend=default` control remained on HTTP PID 23488 with `backend=in_process_windows`;
   - the candidate Runner was then stopped successfully and the final status returned `state=stopped`, `running=false`, `reachable=false`. Phase O therefore has both fail-closed and successful opt-in ChatGPT → Tunnel execution evidence without changing the production default backend.

16. **Phase P — shadow execution / parity telemetry — completed**
   - `run_process` now supports explicit `backend=shadow_candidate`; the primary result is always produced by the stable in-process backend and is never replaced by the shadow result;
   - shadow eligibility is intentionally much narrower than ordinary read-only semantics: only a small set of deterministic local Git structure queries on non-PLA workspace roots are eligible (`rev-parse`, `ls-files`, `ls-tree`, `for-each-ref`, `count-objects`), with no stdin, env overrides, network intent or Git context-override options;
   - Python, pytest, GH, build tools, Git status/diff/log/show and all write/network/environment/specialized actions are excluded from shadow execution in this first version;
   - candidate unavailable/error conditions are telemetry-only (`unavailable` / `error`) and never fail or replace the primary task; ineligible requests record `skipped` and execute only once;
   - comparison telemetry stores only return-code equality plus stdout/stderr byte counts and SHA-256 digests, match booleans, policy metadata and Runner identity; it does not duplicate shadow output content into the event log;
   - `execution_shadow_compared` is persisted through TaskStore, allowing parity evidence to be inspected independently of the immediate tool return;
   - focused shadow/backend/runtime tests: 33 passed; full repository suite: 741 passed;
   - live HTTP reload replaced PID 23488 with PID 2708. A `shadow_candidate` Python Task executed only the in-process primary and recorded `status=skipped`; a strict local Git `rev-parse --is-inside-work-tree` Task returned primary `true` while the candidate Runner was stopped and recorded `status=unavailable`, proving shadow failure does not contaminate the user result;
   - the owner then authorized the positive matched-path E2E. Candidate Runner instance `281b30a5fc7545daa39e1066b4e81b89` started at PID 31256; a workspace `git rev-parse --is-inside-work-tree` Task executed its primary on the in-process backend and its shadow on that independent Runner, producing `status=matched` with identical return code, stdout SHA-256 (`a17fcf0a2f50e2d495e4f90ce263410edc183add6c62699a2facbccf60410f74`) and stderr SHA-256 (`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`); only hashes/lengths were persisted for the shadow comparison;
   - the candidate Runner was stopped successfully after the comparison and the temporary E2E Git repository was removed. Phase P therefore has live skipped, unavailable and matched evidence without changing the production primary backend.

17. **Phase Q — canary/promotion policy — completed**
   - `run_process` adds explicit `backend=canary_candidate`; the production `default` route remains unchanged during promotion testing;
   - promotion v1 is deliberately limited to the strict Phase-P local read-only Git set, and only commands marked `fallback_safe=true` may retry on the in-process backend after candidate failure, preventing ambiguous transport failures from duplicating write side effects;
   - ineligible commands execute exactly once on the stable backend and record `status=skipped`; promoted commands use the candidate first and record either `status=candidate` or `status=fallback` with policy provenance;
   - TaskStore persists `execution_canary_routed`, including actual Runner identity but no command-output duplication;
   - focused canary/backend tests: 38 passed; full repository suite: 746 passed;
   - live E2E after HTTP reload to PID 25904 verified Python is skipped to in-process only, a strict Git query safely falls back while the Runner is stopped, and the same query executes directly on candidate Runner instance `ef4fe2878cb94f85847774d03e1591a2` / PID 16692 when healthy; the Runner was then stopped and the temporary Git repository removed.

18. **Phase R — production Runner lifecycle and reconnectable execution ledger — completed**
   - Runner ownership now records PID, Windows process creation time and image path; Control Plane status re-queries OS identity before trusting durable state, separating `stale_dead_or_reused` from `stale_live`;
   - the Runner holds a PLA-root-scoped Windows named Mutex so a second Runner process cannot concurrently claim the same runtime; stale-dead state may be cleaned and restarted, while stale-live ownership fails closed instead of replacing auth/state;
   - protocol v3 replaces synchronous long-running pipe execution with `submit_resolved` + exact 32-hex `execution_request_id` + `execution_result`; the Runner executes work in a bounded pool, retains completed results, rejects request-ID payload drift, reports active/retained workload, and refuses shutdown while executions are active;
   - Control Plane events now persist `execution_request_id`; `runtime.execution_runner_result` is a read-only capability that retrieves a retained result without re-executing the command;
   - client disconnect/send failure no longer terminates the Runner or discards submitted work; each response is re-bound to the same durable Runner identity/protocol before being trusted;
   - focused lifecycle/ledger/backend tests: 46 passed; full repository suite: 752 passed;
   - live protocol-v3 Runner instance `2586060590824f40b4cf09ed185f8274` / PID 5904 reported verified process identity and `production_ready=true`; old HTTP PID 32152 submitted request `dddddddddddddddddddddddddddddddd` and returned immediately, HTTP restarted to PID 30200, and the new Control Plane retrieved `PHASE_R_DETACHED_STARTED` / `PHASE_R_DETACHED_RECOVERED` from the same Runner via `runtime.execution_runner_result` without resubmitting the command; Runner was then stopped cleanly.

19. **Phase S — production default cutover and restart-safe task ownership — completed**
   - ChatGPT-facing `run_process(backend="default")` now resolves to the independent production Runner, while explicit `backend="in_process"` remains available for diagnostics and emergency rollback; structured PowerShell and persistent sessions keep their existing execution paths;
   - dispatch distinguishes failures before Runner acceptance from indeterminate failures after acceptance. Pre-submit unavailability may safely use the legacy in-process fallback; once an `execution_request_id` has been accepted, potentially mutating work is never re-executed on another backend;
   - same-ID resubmission is idempotent through the Runner ledger. Strict promotion-policy read-only commands may use the previously proven fallback-safe path, while writes and arbitrary code fail closed with the exact request ID for recovery;
   - TaskStore restart recovery is deliberately narrow: only a running single-action production `run_process` task with persisted `execution_started`, `backend_preference=production_runner`, and an exact `execution_request_id` is reattached to the Runner ledger. No command is replayed; all other interrupted tasks retain the previous conservative `TaskInterruptedByRestart` behavior;
   - Runner protocol v4 adds request-ID-scoped cancellation. The Runner stores only its own child `Popen` handle and `cancel_execution(request_id)` terminates that owned child tree; no arbitrary PID termination surface is exposed. `TaskStore.cancel` forwards cancellation only when the persisted task evidence identifies a production Runner request;
   - normal `start_all.ps1` now ensures the production Runner lifecycle, while `stop_all.ps1` requests graceful Runner shutdown and refuses to pretend success while active executions remain. Manual lifecycle capabilities remain privileged and confirmation-gated for operations/diagnostics;
   - focused Phase-S suite: 65 passed; final full repository suite: **762 passed in 117.71s**;
   - live graduation E2E reloaded HTTP from PID 30200 to PID 31544 and started production Runner instance `bf3796eb6a4149919d7e3284d9370732` / PID 15544 on protocol v4. A default `run_process` resolved to `production_runner`, executed `PHASE_S_DEFAULT_RUNNER_OK` in independent child PID 1332, and reported the same Runner identity instead of the HTTP process;
   - restart-recovery E2E submitted task `5224dc28feb940efac3ff4523283e805` under old HTTP PID 31544 with request `5045367a54dd40c9b606f5af13beeb8f`, then restarted HTTP to PID 10384 while the Runner remained unchanged. The new TaskStore emitted `task_recovery_queued`, `task_recovery_started`, and `execution_recovered_after_restart` for the same task/request and completed it from the retained Runner ledger without resubmission;
   - cancellation E2E submitted task `553bcc6997d94b46b55c13d9dcc0a07b` with request `e2eaf9b9a33d44c7ad11ea85aa16eb12`; `cancel_task` emitted `execution_cancellation_forwarded`, the task reached `cancelled`, and the retained Runner result contained only `PHASE_S_CANCEL_STARTED` rather than the later `PHASE_S_CANCEL_SHOULD_NOT_APPEAR`, confirming owned-child termination rather than bookkeeping-only cancellation;
   - final live Runner health is `running=true`, `production_ready=true`, protocol v4, `active_executions=0`, with the same instance/PID after HTTP replacement. Legacy `candidate_runner`, `shadow_candidate`, and `canary_candidate` names remain only as compatibility/diagnostic surfaces.

## Non-goals

- Do not call Codex as PLA's reasoning engine.
- Do not depend on Codex CLI availability for normal PLA operation.
- Do not weaken workspace, Git, transaction, or elevation policy for parity.
- Do not expose arbitrary shell execution merely to imitate Codex.
