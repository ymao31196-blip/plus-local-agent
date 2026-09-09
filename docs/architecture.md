# Agent Runtime v0.8 — Durable Tasks and Transactional Workspace

## Mainline: ChatGPT-native Agent Loop

Agent Runtime v0.8 treats ChatGPT Plus as the brain and `plus-local-agent` as
the hands and controlled execution runtime. The local server does not choose the
next action and does not need a model API key.

```text
User → ChatGPT Plus → reasoning / tool choice → Secure MCP Tunnel
     → MCP → Local Runtime → Task Manager / Workspace Transaction
     → Local Executor → structured observation
     → ChatGPT Plus → next decision or finish
```

The formal primitive layer consists of:

- filesystem: `list_directory`, `read_text`, `write_text`, `replace_text`, and
  single-file `apply_patch` and `apply_changeset`;
- discovery: bounded `search_text`;
- execution: allow-listed `run_process` and structured `run_powershell`;
- orchestration: `execute_actions`, `submit_task`, `task_result`, and `cancel_task`.

Natural-language decomposition and completion decisions remain in ChatGPT.
Filesystem primitives should be preferred over PowerShell for file reads and
mutations. PowerShell exists as a controlled long-tail Windows execution
primitive, not as an arbitrary scripting interface.

## Unified local execution boundary

`local_tools.py` owns every filesystem, search, process, PowerShell, and patch
implementation. `internal_tool_executor.execute_local_tool(tool_name,
arguments)` is the shared structured boundary used by batch actions, background
workers, and the retained experimental agent. MCP wrappers call the same
implementations and do not maintain a second copy of tool behavior.

```json
{"tool": "read_text", "ok": true, "result": {}, "error": null}
```

Validation or execution failures are converted to a stable error containing a
Python exception type name and message; tracebacks are not returned. Process
timeouts and non-zero exits retain their bounded diagnostic result and are also
classified as `ProcessTimeout` or `ProcessExitError`.

`EXECUTABLE_LOCAL_TOOLS` defines the tools available through the unified
executor. A narrower `ACTION_LOCAL_TOOLS` controls composition: `execute_actions`
allows `list_directory`, `read_text`, `write_text`, `replace_text`,
`search_text`, and `run_process`. `run_powershell`, `apply_patch`, and `apply_changeset` deliberately
remain single operations and are rejected from batches. `cancel_task` is a control-layer tool, excluded from both local execution and action batches. `submit_task` may run a
single unified tool or a permitted action batch through the same worker path.

## `search_text`

Input:

```json
{
  "query": "TODO",
  "path": ".",
  "glob": "*.py",
  "case_sensitive": false,
  "max_results": 100
}
```

The path is resolved by `safe_path`. The implementation invokes `rg` with an
argument array, fixed-string matching, JSON output, `shell=False`, and a fixed
30-second execution bound. It stops after detecting one match beyond the
requested limit; `max_results` is constrained to 1–1,000. Missing `rg` produces
`SearchToolUnavailable`. Results contain `status`, `query`, normalized relative
`path`, `matches` (`path`, `line`, `text`), returned `match_count`, and explicit
`truncated`.

## `run_process`

The existing `program`, `args`, `cwd`, and `timeout` interface remains valid.
`workdir` is an alias with clearer semantics; conflicting `cwd` and `workdir`
values are rejected. The working directory must exist below the workspace.

```json
{
  "program": "pytest",
  "args": ["tests"],
  "workdir": ".",
  "timeout": 60,
  "env": {"PYTHONUTF8": "1"},
  "stdin": null
}
```

`env` adds at most 32 string overrides to the inherited environment rather than
replacing it. Names must use portable environment-variable syntax. Overrides of
`PATH`, `PATHEXT`, `COMSPEC`, `PYTHONHOME`, and `PYTHONPATH` are blocked. The
complete host environment is never returned. `stdin` accepts only `null` or a
text string; there is no interactive or persistent terminal.

The program allowlist remains `python`, `pytest`, and `git`, including `.exe`
spellings. Execution uses an argument array and `shell=False`; timeout remains
bounded to 1–300 seconds. `stdout` and `stderr` retain the v0.7 20,000-character
limit, explicit truncation flags, original lengths, return code, and timeout
state.

## Controlled PowerShell v1

`run_powershell` accepts one command with structured parameters:

```json
{
  "command": "Get-ChildItem",
  "parameters": {"LiteralPath": ".", "Recurse": true, "File": true},
  "workdir": ".",
  "timeout": 30
}
```

The command allowlist is:

- `Get-ChildItem`, `Get-Item`, `Test-Path`, `Get-Content`, `Select-String`,
  `Get-FileHash`, `Get-Process`, `Get-Command`, `Get-Location`, `Resolve-Path`,
  and `Get-Date`.

Each cmdlet has a separate parameter-name and value-type allowlist. Every
`LiteralPath` value and `workdir` passes through `safe_path` before execution.
The validated request is serialized as inert JSON data. A fixed, runtime-owned
PowerShell wrapper selects a literal cmdlet branch and splats only validated
parameters. The user cannot provide the wrapper or any script fragment.

There is no script input, pipeline input, redirection, expression evaluation,
assignment, function, loop, script block, dot-sourcing, nested shell, arbitrary
`.NET` call, download, registry mutation, system configuration, or write/delete
cmdlet. In particular, `Invoke-Expression`, `Invoke-Command`, web cmdlets,
`Start-Process`, filesystem mutation cmdlets, job/task commands, `Add-Type`,
`cmd`, `curl`, `wget`, and similar commands are absent from the allowlist.

PowerShell uses the fixed system Windows PowerShell 5.1 executable, no profile,
non-interactive mode, a bounded 1–300 second timeout, and `shell=False`. Output
contains `status`, `command`, `returncode` (or timeout), stdout/stderr, explicit
truncation flags, and original lengths. v1 intentionally has no pipeline schema.

## File metadata and mutation preconditions

`read_text` retains numbered and bounded content and adds:

```json
{"sha256": "...", "mtime": "UTC ISO-8601", "size": 123}
```

`write_text`, `replace_text`, and `apply_patch` accept optional
`expected_sha256`. When supplied, the runtime hashes the current file
immediately before constructing the write. A mismatch raises the structured
`FileChangedSinceRead` error and performs no modification. Omitting the field
preserves v0.7 behavior. Successful mutations return the resulting SHA-256.

## `apply_patch`

`apply_patch` v1 accepts `path`, a single-file unified-diff `patch`, and optional
`expected_sha256`. The path is separate from the patch; optional `---`/`+++`
headers must both match that normalized workspace-relative path. It supports
one or more non-overlapping hunks for an existing UTF-8 text file. Multi-file,
file creation/deletion, binary patches, and patches above 1,000,000 characters
are rejected.

The complete patch is parsed and checked in memory. Every context/removal line
and hunk count must match before any write begins. A mismatch returns
`PatchApplyError`. The completed text is written to a temporary file in the same
directory and atomically replaces the target, so parse or match failure cannot
leave a partial modification. Resolved symlink targets remain subject to
`safe_path`.

## Batch and background execution

`execute_actions` validates 1–100 actions before execution, runs
in order, and defaults to stop-on-error. Its result is `completed`, `partial`,
or `error`, with requested/executed counts and a structured result per action.

`submit_task` returns a task ID after the queued record is committed. The
maximum four ThreadPoolExecutor workers run the shared deterministic executor;
there is no Reasoner, model API, or Codex CLI worker.

### Durable store and restart recovery

The server uses standard-library SQLite at `state/tasks.sqlite3`, outside the
business workspace and ignored by Git. An operator may set `AGENT_TASK_DB` to
another local database path; runtime tools cannot set it. A path inside the
configured workspace is rejected. SQLite uses WAL, FULL synchronous writes,
parameterized statements, a per-store RLock, and a lifetime OS file lock. A second
runtime cannot take ownership and incorrectly recover another live owner's tasks.

Rows preserve `task_id`, `status`, `created_at`, `started_at`, `finished_at`,
`request`, `result`, and `error`; `cancel_requested` is additive. Timestamps are
UTC ISO-8601. Initialization at server startup changes all old queued/running
rows to `failed` with `TaskInterruptedByRestart`, finished time, and an event.
No requests are replayed. Hard-crash side effects or surviving descendants may
exist; recovery does not reattach or kill stored PIDs. Completed, failed, and
cancelled records remain queryable after a new store/server starts.

Malformed database/schema/JSON and I/O errors return `TaskStoreError`. The
server's other tools can start even when storage initialization fails; affected
task tools fail closed. A failed terminal write is never presented as an actively
running worker after that worker exits. No corrupted database is silently erased.
`TaskStore(db_path=temporary_path)` supports persistence tests; its no-path
embedded default is `:memory:`. Server tests inject temporary stores and pass
isolated database paths to child servers, avoiding the real state directory.

Active tasks are bounded to 256; requests to 1,000,000 serialized characters;
batches to 100 actions. Oversize requests/batches and a full queue return explicit
errors. Results exceeding 2,000,000 serialized characters become an explicitly
labelled JSON tail with `truncated`, `original_length`, and `encoding` metadata.
History is not held in an ever-growing in-memory dictionary. Historical task rows
remain on disk; automatic archival/retention is a future operator policy.

### Cancellation and process lifecycle

`cancel_task({"task_id":"..."})` is the only cancellation interface. Queued tasks
become `cancelled` without entering execution. For running tasks it first persists
`cancel_requested`, then signals their private context and terminates only child
process handles registered by that context. The response may remain `running`
until the worker acknowledges cancellation and records terminal `cancelled`.
Repeated cancellation of completed/failed/cancelled tasks returns the unchanged
record. Unknown IDs return `TaskNotFound`; no PID parameter or kill tool exists.

Worker-local context is reset in a finally block. Cancellation before process
registration also kills the subsequently registered child. `run_process` and
controlled PowerShell in tasks use `Popen(shell=False)`, bounded pipe readers,
private temporary stdin, timeout termination, and child reaping. Reader threads
poll runtime-owned handles and stop even when a descendant retains a pipe. That
condition is an explicit capture error. Output tails remain 20,000 characters,
with full decoded character counts; process text normalizes newlines. Existing
synchronous calls keep the v0.7 `subprocess.run` path. Search registers its existing
rg child with the same task context. All program, workdir, environment, stdin,
PowerShell, and 1–300 second timeout validations still precede execution.

Only direct children are terminated. There is no process-tree guarantee. File
operations cooperate at execution boundaries; Python threads are not forcibly
killed. Cancellation never undoes earlier writes. A cancelled batch retains its
executed actions and skips subsequent ones. Changeset commit/rollback defers
cancellation until the transaction reaches a reportable outcome; its result must
be inspected even when the surrounding task is cancelled.

### Incremental observations

`task_result(task_id)` retains the full legacy record. Optional
`task_result(task_id, cursor=0)` returns task status/timestamps, error,
`cancel_requested`, `events`, `next_cursor`, `has_more`, `oldest_cursor`,
`events_truncated`, `dropped_through_cursor`, and `result_available`. Cursor mode
omits the request and final result to avoid retransmitting large text. Fetch the
full record without cursor when the final result is needed.

Events have `cursor`, `type`, `created_at`, and `data`. Cursor IDs are monotonically
increasing database-wide and may have gaps between tasks. Queries return only IDs
strictly greater than the supplied cursor, at most 64 events per page. Continue
with `next_cursor` while `has_more` is true. At most 256 events per task are kept.
An outdated cursor explicitly reports a lost-history gap and the dropped-through
cursor. Oversize event data is labelled as a truncated JSON tail; stdout/stderr
also retain their original-length and truncation metadata.

Events include task_queued, task_started, action_started, action_completed
(with ok/error), stdout, stderr, cancellation_requested, changeset phase events,
and task_completed/task_failed/task_cancelled. Observations are **stage-level**:
stdout/stderr arrive when a process action finishes, not as live line streaming.
While it runs, task/action-started events establish progress without a second
execution architecture or unbounded log accumulation.

## Transactional multi-file changeset with rollback

```json
{"changes":[
  {"path":"src/a.py","expected_sha256":"<64 hex characters>","patch":"@@ -1 +1 @@\n-old\n+new\n"},
  {"path":"src/b.py","expected_sha256":"<64 hex characters>","patch":"@@ -1 +1 @@\n-old\n+new\n"}
]}
```

`apply_changeset` is a separate MCP decision boundary. It may also be submitted
as a single task, but is explicitly absent from `ACTION_LOCAL_TOOLS`. Each change
requires exactly path, expected_sha256, and patch. v1 supports only existing UTF-8
workspace files, at most 32 targets, 1,000,000 total patch characters, and
16,000,000 total source bytes. Creation/deletion of paths, rename, binary and
multi-file diffs are rejected. Editing an existing file to empty is permitted.
Duplicate resolved paths and hardlink aliases are rejected.

1. **Preflight:** validate every resolved safe path, existence, UTF-8, required
   SHA-256, headers, exact context, old/new hunk positions and counts, and limits.
   A failure writes no official file. Parsing reuses the single-file patch engine,
   including CRLF and no-final-newline handling.
2. **Stage:** create candidate and original-byte backup files in each target's
   directory, flush and fsync them. Recheck every original hash and resolved path
   after all staging, before any official replacement. Staging failure leaves
   official files untouched and cleans up owned temporary files.
3. **Commit:** under the same process-local mutation RLock used by write_text,
   replace_text, and apply_patch, revalidate each target and replace it with
   `os.replace`. In-process primitive mutations cannot interleave. Bytecode cache
   invalidation follows replacements. No local Reasoner is involved.
4. **Rollback:** on any commit error, restore applied files in reverse order using
   their staged original bytes. Check the installed hash/path before restoration
   so an external intervening edit is not overwritten. Report restoration errors;
   keep backup files for targets that could not be restored, exposing their paths
   as `recovery_files`. Cleanup failures are explicit too.

Success returns status=completed, files_requested, files_applied, rolled_back=false,
and per-file path/before_sha256/after_sha256 results. Failures include error type,
message, failed_phase, path, files_replaced_before_rollback, rollback_attempted,
rolled_back, restored_paths, rollback_errors, unrestored_paths, and recovery_files.
`files_applied` on failure counts replacements not successfully restored; it does
not claim an externally modified file still has the candidate bytes.

This is **not an OS-atomic multi-file commit**. External editors/processes do not
honor the Python lock. Hash checks catch stale data before committing and again
at each replacement, but cannot eliminate an external check/replace race or a
system crash between replacements. There is no crash journal or automatic
changeset recovery. Cancellation and restart status never imply rolled-back files.

## Security boundary

- All filesystem and PowerShell path inputs resolve below `AGENT_WORKSPACE`
  through `safe_path`; resolved outside targets and symlink escapes are rejected.
- Processes use argument arrays and `shell=False`. There is no arbitrary shell,
  `cmd.exe`, Bash, arbitrary PowerShell, or interactive terminal.
- Read and process observations are bounded and never silently truncated.
- Mutations can use optimistic SHA-256 preconditions; patch application is
  validated fully before atomic replacement.
- No delete tool, process kill tool, package installation, network download,
  browser, model API, provider key, Codex CLI Agent, registry operation, service
  management, Git commit/push/reset/clean, or server-side Reasoner mainline was
  added.
- Secure MCP Tunnel endpoints and credentials are unchanged.

## Experimental: server-side Agent Loop via MCP Sampling

The v0.5/v0.6 `probe_sampling`, `run_agent_task`, `MCPSamplingBackend`,
`GenericLLMReasoner`, prompt builder, decision parser, Agent Service, and fake
Sampling client remain unchanged for experiments. This is not the formal v0.8
mainline. No API-key fallback was added.

## Relationship to Engineering Bridge

Engineering Bridge remains read-only reference material. No Engineering Bridge
code was modified or invoked. The runtime shares only general task/result ideas;
ChatGPT retains ownership of all natural-language planning and next-step
decisions.
