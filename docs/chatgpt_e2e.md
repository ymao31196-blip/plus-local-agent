# ChatGPT-native Agent Loop E2E

This is the v0.7 human acceptance test. ChatGPT is the Reasoner; the local MCP
server is only the controlled executor.

## Setup

1. Use the `plus-local-agent` Conda environment.
2. Run `./start_http.ps1`, then `./start_tunnel.ps1`.
3. Connect or refresh the existing `plus-local-agent` Secure MCP Tunnel in ChatGPT.
4. Confirm `diagnose_client`, the five low-level tools, `execute_actions`,
   `submit_task`, and `task_result` are visible.
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

## Optional long-task check

Submit the pytest Action with `submit_task`, retain its `task_id`, and call
`task_result(task_id)` until it reports `completed` or `failed`. The server should
not claim to reason about or repair the failure; ChatGPT decides the next Action.

## Experimental Sampling check

`probe_sampling` and `run_agent_task` are retained from v0.5/v0.6 but are no longer
part of this acceptance path. If testing them separately, first call
`diagnose_client`. A real ChatGPT client that does not advertise MCP Sampling
cannot run the server-side experimental loop; do not add an API-key fallback.
