# PLA Python Source

This directory contains PLA's first-party Python implementation. New first-party Python modules belong under `src/`, never at the repository root.

The repository was converted from a historically flat module layout into domain packages in small, independently testable steps. Phase 1 moved all Python implementation under `src/`; Phases 2–8 extracted Execution, Capability/Routing, Runtime/Hooks/Transactions, Project, Artifact/Browser, Agent/Reasoning, and Provider domains; Phase 9 completed the layout by extracting Host, Tooling, MCP runtime support, and Diagnostics while keeping `server.py` as the stable application entrypoint.

Current boundaries:

- `execution/`: command classification, execution policy, one-shot/process backends, Execution Runner transport/runtime/service, process identity, ConPTY integration, shadow/candidate promotion policy, and Execution Runner capability exposure.
- `capabilities/`: Capability Broker, registry/models, core capability exposure, browser/provider/runtime/session adapters, local mutation surfaces, and governed Windows actions.
- `routing/`: declarative routing, capability steering, and routing audit logic.
- `agent/`: agent loop orchestration and reusable agent service.
- `reasoning/`: decision parsing, standardized reasoner interfaces, prompt construction, model backends, rule-based reasoning, generic LLM reasoning, and test/fake backends.
- `runtime/`: runtime context/lifecycle/parity, persistent sessions, durable task store, and event store.
- `hooks/`: observer/gate hook runtimes, external observer runtime, and observer plugin manifests.
- `transactions/`: durable transaction state and capability transaction envelopes.
- `project/`: durable project state, acceptance contracts/evaluations, structured verification specs, independent verification, and transactional multi-file changesets.
- `artifacts/`: artifact export bridge, artifact policy, immutable snapshot runtime, integrity metadata, and handoff support.
- `browser/`: browser runtime lifecycle, persistent session keeper, and browser download recovery/integration.
- `provider/`: PLA-internal provider manifests, discovery/hot-plug runtime, provider doctor, reviewed setup/runtime helpers, source-backed provider installation, and fake/test provider support. Concrete provider servers remain in the repository-level `providers/` directory.
- `host/`: local Windows/desktop integration, the Interactive Elevation Broker, the Computer Use indicator, and named workspace-root policy.
- `tooling/`: shared local tool implementations, the unified internal executor, tool registry metadata, and tool-schema normalization.
- `mcp_runtime/`: MCP client/session management for external providers. The package is deliberately not named `mcp/`, because PLA imports the third-party `mcp` library and must not shadow it on `PYTHONPATH=src`.
- `diagnostics/`: opt-in local E2E diagnostics.
- `server.py`: the only Python module intentionally left directly under `src/`; it is the stable MCP/HTTP application entrypoint.

Package extraction is intentionally domain-by-domain. Do not move unrelated modules merely to make the tree look symmetrical; each package move must preserve runtime paths, executable entrypoints, security behavior, and focused regression coverage.
