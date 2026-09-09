# Agent Runtime v0.7 Architecture

## Mainline: ChatGPT-native Agent Loop

Agent Runtime v0.7 treats ChatGPT Plus as the brain and `plus-local-agent` as
the hands and controlled execution runtime. The local server does not choose the
next action and does not need a model API key.

```text
User
  ↓
ChatGPT Plus
  ↓
Reasoning / Planning
  ↓
Secure MCP Tunnel
  ↓
plus-local-agent
  ↓
Local Tool Executor
  ↓
Filesystem / Allow-listed Process
  ↓
Structured Observation
  ↓
ChatGPT Plus
  ↓
Next Decision or Finish
```

Simple operations use one of `list_directory`, `read_text`, `write_text`,
`replace_text`, or `run_process`. Short, predetermined sequences use
`execute_actions`. Potentially slow structured work uses `submit_task`, followed
by one or more `task_result` calls. Natural-language decomposition always remains
in ChatGPT.

## Unified local execution boundary

`local_tools.py` owns the filesystem and process implementations.
`internal_tool_executor.execute_local_tool(tool_name, arguments)` is the shared
controlled entry used by batch actions, background workers, and the retained
in-server experimental agent. It returns:

```json
{"tool": "read_text", "ok": true, "result": {}, "error": null}
```

The explicit `EXECUTABLE_LOCAL_TOOLS` allowlist contains only
`list_directory`, `read_text`, `write_text`, `replace_text`, and `run_process`.
Control-layer tools cannot be recursively executed. Every allowed action still
passes through `safe_path`, the existing program allowlist, `shell=False`, and
the bounded process timeout.

## `execute_actions`

Input schema:

```json
{
  "actions": [
    {"tool": "read_text", "arguments": {"path": "agent_test/calculator.py"}},
    {"tool": "run_process", "arguments": {"program": "pytest", "args": ["agent_test"]}}
  ],
  "stop_on_error": true
}
```

`actions` must be a non-empty list. Every item must contain a non-empty string
`tool` and an object `arguments`. Actions run sequentially. `stop_on_error`
defaults to `true`; if false, later Actions still run after a failure. A process
timeout or non-zero exit code is an Action failure, while its structured process
result is retained for diagnosis.

Output schema:

```json
{
  "status": "completed | partial | error",
  "actions_requested": 2,
  "actions_executed": 2,
  "results": [{
    "index": 0, "tool": "read_text", "arguments": {},
    "ok": true, "result": {}, "error": null
  }]
}
```

`completed` means every requested Action succeeded. `partial` means at least one
succeeded and at least one failed. `error` means none succeeded. With stop-on-error,
`actions_executed` makes skipped trailing Actions explicit.

## Background structured tasks

`submit_task` accepts exactly one of these forms:

```json
{"tool": "run_process", "arguments": {"program": "pytest", "args": ["agent_test"]}}
```

```json
{"actions": [{"tool": "read_text", "arguments": {"path": "x.txt"}}], "stop_on_error": true}
```

It immediately returns `{"task_id":"...","status":"queued"}`. A bounded
in-process thread pool executes the same unified local executor; it does not run
Codex CLI and has no Reasoner. The first version intentionally uses only an
in-memory store, so records do not survive a server restart.

Each record contains `task_id`, `status`, `created_at`, `started_at`,
`finished_at`, `request`, `result`, and `error`. States are `queued`, `running`,
`completed`, and `failed`.

`task_result` input is `{"task_id":"..."}`. It returns the current full record.
An unknown ID returns a structured `TaskNotFound` error rather than raising a
Python exception. The server never notifies ChatGPT proactively; ChatGPT retains
the ID and polls when it is ready to continue reasoning.

## Output bounds

Text file reads, process stdout, and process stderr are limited to the most recent
20,000 characters. Results expose `truncated`/`stdout_truncated`/
`stderr_truncated` plus the corresponding original length. Truncation is never
silent. Process results continue to include `returncode`, `stdout`, and `stderr`.

## Security boundary

- All filesystem paths resolve below `AGENT_WORKSPACE` through `safe_path`.
- The existing process allowlist remains `python`, `pytest`, and `git` (including
  `.exe` spellings).
- Processes use argument arrays and `shell=False`; no command string evaluation,
  `eval`, or `exec` is used.
- Timeout remains clamped to 1–300 seconds.
- No delete tools, arbitrary shells, model APIs, credentials, browser control,
  or unrestricted network-download capability are added.
- No Git write abstraction was added. Existing `git` process exposure is unchanged.
- Secure MCP Tunnel endpoints and credentials are unchanged.

## Experimental: server-side Agent Loop via MCP Sampling

The v0.5/v0.6 `probe_sampling`, `run_agent_task`, `MCPSamplingBackend`,
`GenericLLMReasoner`, prompt builder, decision parser, Agent Service, and fake
Sampling client remain for experiments and possible future use.

```text
MCP Client with Sampling support
  ↓
run_agent_task → MCP Sampling / MRTR → GenericLLMReasoner
  ↓
Local Tool Executor
```

This is not the v0.7 mainline. The real ChatGPT client currently does not advertise
MCP Sampling for this integration, so a server-side Agent Loop is currently
unavailable there. `diagnose_client` remains callable and reports the factual
client capability and the v0.7 mainline label.

## Relationship to Engineering Bridge

The design shares Engineering Bridge's task abstraction, structured results,
task IDs, status polling, background worker concept, timeout handling, and error
mapping. Engineering Bridge remains read-only reference material.

The core difference is ownership of reasoning: Engineering Bridge can dispatch
Codex CLI work, while this runtime executes only already-structured local Actions.
ChatGPT performs every natural-language planning and next-step decision. No
Engineering Bridge code or Codex CLI invocation is used.
