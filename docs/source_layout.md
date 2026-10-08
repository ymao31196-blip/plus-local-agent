# PLA Source Layout

PLA uses a deliberately shallow repository root and keeps implementation code away from operational entrypoints.

## Repository boundary

```text
plus-local-agent/
├─ src/                 # Python implementation modules
├─ scripts/             # low-level runtime start/stop helpers
├─ providers/           # provider server implementations
├─ provider_manifests/  # provider runtime declarations
├─ provider_specs/      # reviewed provider dependency specs
├─ tests/               # automated tests
├─ docs/                # architecture, operations and release notes
├─ config/              # checked-in configuration templates
├─ state/               # runtime-generated state; not source
├─ workspace/           # default controlled workspace; not PLA source
├─ install.ps1          # customer installation entrypoint
├─ setup_providers.ps1  # provider setup entrypoint
├─ start_all.ps1        # normal runtime start entrypoint
├─ stop_all.ps1         # normal runtime stop entrypoint
└─ restart_pla.ps1      # controlled PLA HTTP restart entrypoint
```

The repository root must not contain Python implementation modules. Low-level lifecycle helpers belong in `scripts/`; only user-facing operational entrypoints stay at the root.

## `src/` responsibility map

Phase 1 moved all Python implementation under `src/`. Phases 2–8 extracted Execution, Capability/Routing, Runtime/Hooks/Transactions, Project, Artifact/Browser, Agent/Reasoning, and Provider domains. Phase 9 completed the package layout by extracting Host integration into `src/host/`, shared tool implementation into `src/tooling/`, MCP client support into `src/mcp_runtime/`, and opt-in diagnostics into `src/diagnostics/`. `src/server.py` remains the single stable top-level Python entrypoint.

### Agent orchestration

`src/agent/` contains `agent_loop.py` and `agent_service.py`. The CLI adapter launches the MCP server from `src/server.py` while retaining the repository root as its working directory.

### Reasoning

`src/reasoning/` contains `decision_parser.py`, `generic_llm_reasoner.py`, `llm_interface.py`, `llm_reasoner.py`, `model_backend.py`, `prompt_builder.py`, `reasoner.py`, `rule_reasoner.py`, and `fake_model_backend.py`. Tool-schema normalization now lives in `src/tooling/tool_schema.py` because it belongs to the shared tool boundary rather than reasoning itself.

### Capability plane

`src/capabilities/` contains `browser_runtime_capabilities.py`, `capability_broker.py`, `capability_models.py`, `capability_registry.py`, `core_capabilities.py`, `external_process_capabilities.py`, `local_mutation_capabilities.py`, `observer_runtime_capabilities.py`, `provider_runtime_capabilities.py`, `runtime_lifecycle_capabilities.py`, `session_runtime_capabilities.py`, and `windows_action_capabilities.py`.

### Routing plane

`src/routing/` contains `capability_steering.py`, `declared_routing.py`, and `routing_audit.py`. Capability IDs such as `core.routing_audit` remain stable and are not Python module paths.

### Execution plane

`src/execution/` contains `command_semantics.py`, `conpty_backend.py`, `execution_backend.py`, `execution_program_policy.py`, `execution_runner_capabilities.py`, `execution_runner_runtime.py`, `execution_runner_service.py`, `execution_runtime.py`, `process_controller.py`, `promotion_execution_policy.py`, `runner_process_identity.py`, `runner_transport.py`, `semantic_execution_policy.py`, and `shadow_execution_policy.py`. The standalone Runner service bootstraps the parent `src/` path before importing the package so it remains executable as an isolated process entrypoint.

### Runtime

`src/runtime/` contains `event_runtime.py`, `runtime_context.py`, `runtime_lifecycle.py`, `runtime_lifecycle_broker.py`, `runtime_parity.py`, `session_runtime.py`, and `task_store.py`. Runtime-generated databases and lifecycle state continue to resolve against the repository-level `state/` directory rather than creating package-local state.

### Hooks

`src/hooks/` contains `external_observer_runtime.py`, `gate_hook_runtime.py`, `observer_hook_runtime.py`, and `observer_plugin_manifest.py`.

### Transactions

`src/transactions/` contains `transaction_action_envelope.py` and `transaction_runtime.py`. Provider and MCP integration remain separate architectural domains rather than being folded into the core Runtime plane.

### Artifacts

`src/artifacts/` contains `artifact_bridge.py`, `artifact_policy.py`, and `artifact_runtime.py`. Artifact export, integrity metadata, immutable snapshots, resource-link handoff, and governance remain behind the same public MCP tool names.

### Browser

`src/browser/` contains `browser_download.py`, `browser_runtime.py`, and `browser_session_keeper.py`. Browser state continues to resolve against repository-level `state/browser`, and the PowerShell start/stop helpers import `browser.browser_runtime` through `PYTHONPATH=src`.

### Project state and verification

`src/project/` contains `acceptance_contract.py`, `changeset_manager.py`, `independent_verifier.py`, `project_state.py`, and `verification_spec.py`. Public tool names such as `project_state_get` and Capability IDs such as `core.project_state_get` remain stable; only the internal Python module namespace changed.

### Provider integration

`src/provider/` contains `external_provider_runtime.py`, `fake_capability_provider.py`, `provider_doctor.py`, `provider_manifest.py`, `provider_setup_runtime.py`, `setup_source_provider.py`, and `source_provider_setup.py`. This package owns PLA-internal provider discovery, manifests, health/setup logic, hot-plug runtime, and reviewed source-backed installation. Concrete provider MCP servers remain under the repository-level `providers/` directory. `setup_providers.ps1` invokes the source-backed setup CLI as `python -m provider.setup_source_provider` with a temporary `PYTHONPATH=src` and restores the prior environment afterward.

### Host integration

`src/host/` contains `computer_use_indicator.py`, `interactive_elevation_broker.py`, and `workspace_manager.py`. Host modules continue to resolve runtime state against the repository-level `state/` directory. `scripts/start_elevation_broker.ps1` points at the packaged broker, and the Computer Use indicator relaunches its packaged file directly so it does not depend on inherited `PYTHONPATH` state.

### Tooling foundation

`src/tooling/` contains `internal_tool_executor.py`, `local_tools.py`, `tool_registry.py`, and `tool_schema.py`. This is the shared local execution/tool boundary used by MCP wrappers, durable workers, capabilities, and the retained agent runtime.

### MCP runtime support

`src/mcp_runtime/` contains `mcp_client_manager.py`. The package is deliberately named `mcp_runtime` rather than `mcp`: PLA imports the third-party `mcp` package, so a local `src/mcp/` package would shadow that dependency when `src` is on `PYTHONPATH`.

### Diagnostics

`src/diagnostics/` contains `e2e_debug.py`, which remains opt-in and local-only.

### Main entrypoint

`src/server.py` remains the MCP/HTTP application entrypoint and is intentionally the only Python module directly under `src/`. Operational scripts and tests may rely on this stable path while implementation code stays inside domain packages.

## Refactor rules

1. New Python implementation code goes under `src/`, never the repository root.
2. Runtime-generated files go under `state/`, `workspace/`, provider environments, or another explicitly ignored runtime directory; they are not source files.
3. User-facing operational scripts may remain at the root. Component-level start/stop helpers go under `scripts/`.
4. Provider-specific implementation stays under `providers/` unless it becomes part of PLA core.
5. Deep package extraction is performed domain by domain. A directory cleanup must not silently change the public MCP surface, security policy, provider contract, or runtime state location.
6. Each package extraction must preserve or deliberately migrate imports, update executable paths, and run its focused tests before proceeding to the next domain.

The layout guard in `tests/test_source_layout.py` enforces the repository-level rules so the root does not gradually become flat again.

## Runtime entrypoint contract

Component scripts resolve the project as the parent of `$PSScriptRoot`. The root
orchestrators resolve their own directory; all script references include
`scripts/`. Python helpers import `scripts/runtime_common.psm1` and call
`Initialize-PlaPythonEnvironment` before launching Python. This prepends the
absolute project `src` directory once and preserves other `PYTHONPATH` entries.

Broker and HTTP launches retain absolute file entrypoints under `src` so their
CommandLine ownership checks match the same paths at startup, stop, and restart.
Browser Runtime resolves its keeper beside `browser_runtime.py`, while profile,
state, provider environment, and output paths remain relative to the project
root. `start_all.ps1` waits for the Browser supervisor's complete readiness check
(including the keeper session), so Python failures are visible immediately.

Execution Runner's Python launcher supplies its child's `PYTHONPATH` and working
directory explicitly, including when invoked by HTTP or another Python client.
The service does not patch `sys.path` to compensate for its location.

Tunnel start and stop use the same `Resolve-PlaTunnelConfig` function: the default
is `config/tunnel.local.yaml`, and relative `PLA_TUNNEL_CONFIG` overrides resolve
against the project root. Credentials and Tunnel configuration contents are not
changed by this layout handling. After ownership verification, HTTP stop/restart
also closes its provider process tree. Broker stop scripts mark their status
files `stopped` and clear the PID after confirming process exit.

Run integration tests with the production PLA chain stopped: detached Runner
tests share the project's named mutex and intentionally cannot start a second
Runner beside a running production instance.
