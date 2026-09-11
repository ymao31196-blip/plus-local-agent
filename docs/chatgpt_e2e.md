# ChatGPT-native Agent Loop E2E

These manual Agent-loop scenarios remain part of the v1.0 acceptance boundary.
They originated in v0.8 (including the v0.7 scenario). ChatGPT is the Reasoner; the local MCP
server is only the controlled executor.

## Setup

1. Use the `plus-local-agent` Conda environment.
2. Run `./start_http.ps1`, then `./start_tunnel.ps1`.
3. Connect or refresh the existing `plus-local-agent` Secure MCP Tunnel in ChatGPT.
4. Confirm `diagnose_client`, the filesystem tools, `search_text`, `run_process`,
   `run_powershell`, `apply_patch`, `execute_actions`, `submit_task`, and
   `task_result`, `cancel_task`, and `apply_changeset` are visible.
5. Confirm `workspace/agent_test/calculator.py` contains `return a - b`.

The project-level `pytest.ini` intentionally collects only `tests/`, so the broken
human fixture does not invalidate the runtime regression suite.

## Acceptance prompt

Send this exact natural-language task in ChatGPT:

> 检查 agent_test 为什么测试失败，修复并重新运行测试直到通过。

ChatGPT may choose any sound trace. Typical traces are:

```text
read_text → run_process(pytest) → replace_text → run_process(pytest)
```

or:

```text
execute_actions(read + pytest) → execute_actions(replace + pytest)
```

For the pytest Action, use `program: "pytest"`, `args: ["agent_test"]`, and the
default workspace cwd. Acceptance requires that ChatGPT independently interprets
the first non-zero `returncode`, changes subtraction to addition, reruns pytest,
observes `returncode: 0`, and only then finishes.

After acceptance, reset the fixture to `return a - b` so the scenario remains
reproducible.

The original v0.7 acceptance trace remains valid without hashes. A safer client
may retain the `sha256` returned by `read_text` and pass it as
`expected_sha256` to `replace_text` or `apply_patch`. A stale hash must be
reported as `FileChangedSinceRead`, after which ChatGPT should read again before
deciding whether to retry.

For discovery, ChatGPT may call `search_text` directly or include it in
`execute_actions`. Prefer dedicated filesystem tools for file content and
mutation. `run_powershell` is only for an allow-listed read-only Windows cmdlet;
it has no `script` or pipeline input and is intentionally unavailable inside
`execute_actions`.

## Optional long-task check

Submit the pytest Action with `submit_task`, retain its `task_id`, and call
`task_result(task_id)` until it reports `completed`, `failed`, or `cancelled`. The server should
not claim to reason about or repair the failure; ChatGPT decides the next Action.

## Experimental Sampling check

`probe_sampling` and `run_agent_task` are retained from v0.5/v0.6 but are no longer
part of this acceptance path. If testing them separately, first call
`diagnose_client`. A real ChatGPT client that does not advertise MCP Sampling
cannot run the server-side experimental loop; do not add an API-key fallback.


## v0.8 E2E 1: Task cancellation

Copy `docs/fixtures/long_task.py` into a dedicated workspace test directory, for
example `workspace/runtime_v08/long_task.py`. This fixture only prints and sleeps;
it performs no network or filesystem changes.

Example acceptance prompt:

> 在 runtime_v08 中启动那个长任务，确认它开始执行后取消，并确认最终状态。不要终止其他进程。

Success conditions (not a mandatory tool order):

- An allowed Python process executes the fixture through `submit_task`.
- While running, a cursor observation exposes task_started/action_started.
- `cancel_task` addresses only the returned task ID. Its immediate response may
  show running with cancel_requested=true; ChatGPT waits for terminal cancelled.
- The owned direct child has stopped; repeating cancellation is idempotent.
- ChatGPT does not equate a cancellation request with completed cancellation.
  stdout is a phase-level tail delivered after the process ends, not live streaming.

For example, a structured submission may use tool=run_process,
arguments={program:"python", args:["runtime_v08/long_task.py"], timeout:120}.
Use `task_result(task_id,cursor=0)` and carry `next_cursor` forward; fetch
`task_result(task_id)` for the full final result. A record and its cancelled state
must remain queryable after the operator normally restarts the server. Do not
restart the shared tunnel or alter credentials for this test.

## v0.8 E2E 2: Two-file changeset

Copy the entire `docs/fixtures/changeset_demo` folder to
`workspace/changeset_demo`. Its two intentionally broken modules are math_a.py
(addition incorrectly subtracts) and math_b.py (doubling incorrectly adds two).
`test_demo.py` verifies both. Keep `workspace/agent_test` unchanged.

Example acceptance prompt:

> 检查 changeset_demo 中的两个计算函数，使用带 sha256 前置条件的多文件 ChangeSet 修复它们，并验证测试通过。

Success conditions, without requiring one fixed sequence:

- ChatGPT obtains the current contents and hashes of both files and identifies
  both bugs; each changeset entry includes the hash returned by read_text.
- One standalone `apply_changeset` changes both modules to addition and
  multiplication by two. The transaction reports two applied files with matching
  before/after hashes. It is not nested inside execute_actions.
- Running allowed pytest against `changeset_demo` reports returncode=0 and
  `2 passed`. ChatGPT uses this observation to decide the task is complete.
- A deliberate stale hash in either entry must reject the whole preflight with
  FileChangedSinceRead and zero official changes. ChatGPT reads again before
  deciding how to proceed.
- Recopy only this dedicated fixture from docs when repeating the scenario;
  do not reset or clean the repository or touch unrelated workspace files.

The repository pytest suite still only collects tests/. Both the original and
new broken manual fixtures are excluded. Automated verification copies this
new fixture to a temporary workspace, observes both failures, applies a single
changeset, and verifies both tests pass. Live ChatGPT acceptance remains a human
step; automated MCP/pytest coverage is not evidence of a real ChatGPT run.

## PLA Self-Maintenance E2E

Example acceptance prompt:

> 检查 PLA 项目中的某个测试 fixture 为什么失败，修改 PLA 源码并运行对应测试直到通过。

Use `root="pla"` for source discovery, reads, mutations, patches/changesets, and
the test process workdir. The exact tool order is intentionally not prescribed.
Retain hashes returned by `read_text` when changing existing files, and use a
temporary/dedicated fixture rather than intentionally damaging a passing source
file merely to demonstrate the workflow.

Success conditions:

- ChatGPT reads and understands the relevant PLA source through the PLA root.
- ChatGPT changes the intended PLA source through the same root and observes a
  successful mutation result.
- An allowed process runs the relevant tests with a legal PLA-root workdir and
  reports PASS.
- `.git`, runtime databases/state, credential/secret-like files, caches, and
  `config/tunnel.yaml` are not modified through PLA filesystem tools.
- The current PLA Server does not hot-reload or restart itself; changed code is
  loaded only after a later operator/external safe restart.

The diagnostic tool may report root names and read/write/execute capability
booleans. It intentionally does not expose their physical paths.
