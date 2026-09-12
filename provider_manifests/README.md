# Provider Manifest Schema (v1)

PLA v1 loads external MCP providers from `provider_manifests/*.json`.

A provider manifest is declarative configuration. It does not contain credentials
and it does not install dependencies. Dependencies are pinned separately under
`provider_specs/<provider_id>.txt` and installed into
`.provider_envs/<provider_id>/`.

## Minimal shape

```json
{
  "schema_version": 1,
  "id": "example",
  "autostart": true,
  "mode": "legacy",
  "runtime": {
    "kind": "isolated_python_stdio",
    "python": ".provider_envs/example/Scripts/python.exe",
    "args": ["-m", "example_mcp"],
    "cwd": "."
  },
  "tool_allowlist": ["one_tool"],
  "tool_overrides": {}
}
```

## Executable stdio providers

Non-Python MCP servers can use the same broker and capability contracts:

```json
{
  "schema_version": 1,
  "id": "example-exe",
  "autostart": false,
  "mode": "auto",
  "runtime": {
    "kind": "executable_stdio",
    "command": "C:/Program Files/Example/example-mcp.exe",
    "args": [],
    "cwd": "."
  },
  "tool_allowlist": ["one_tool"],
  "tool_overrides": {}
}
```

`command` may be an absolute executable path, a path relative to the PLA project
root, or a bare executable name resolved from `PATH`. The process is started
directly through stdio; PLA does not insert a shell. The provider working
directory remains constrained to the PLA project root.

## Selection

`PLA_EXTERNAL_PROVIDERS=*` enables every manifest with `autostart: true`.
A comma-separated value such as `markitdown,docx` explicitly selects providers.
An unknown provider id is rejected.

## Security constraints

- `isolated_python_stdio` providers must use the Python interpreter inside
  `.provider_envs/<provider_id>/`.
- `executable_stdio` providers may point at an explicitly reviewed external
  executable; relative command paths remain inside the PLA project root.
- Provider working directories must remain inside the PLA project root.
- Only allowlisted remote tools are registered when `tool_allowlist` is present.
- Tool overrides outside the allowlist are rejected.
- `requires_confirmation: true` requires explicit `INVOKE` approval.
- `requires_transaction: true` rejects direct broker invocation and requires
  the Transaction Action Envelope. New orchestration should normally enter it through
  `core.transaction_invoke` on the stable capability surface; the legacy direct
  `transaction_invoke_capability` MCP tool remains available for compatibility.
  Transaction gating can be combined with explicit confirmation.
- Artifact input/output policy remains enforced by the Capability Broker.
- `artifact_contract.policy` can declare bounded size, count, TTL and MIME limits.
- Provider policies may tighten limits but cannot exceed PLA hard caps.
- Credentials must not be stored in manifests.

## Adding a provider

For an isolated Python provider:

1. Add `provider_specs/<provider_id>.txt`.
2. Add `provider_manifests/<provider_id>.json`.
3. Run `setup_providers.ps1`.
4. While PLA is running, invoke `runtime.provider_rescan` with explicit
   `INVOKE` confirmation. No HTTP restart is required.

For an executable provider:

1. Install or place the reviewed MCP executable.
2. Add `provider_manifests/<provider_id>.json` with
   `runtime.kind = "executable_stdio"`.
3. Invoke `runtime.provider_rescan` with explicit `INVOKE` confirmation.

Use `runtime.provider_reload` after editing an active manifest, and
`runtime.provider_enable` / `runtime.provider_disable` for temporary
process-local availability changes. Removing a selected manifest followed by
`runtime.provider_rescan` removes that provider from the runtime and Capability
Registry.

No PLA Python runtime code change is required for either supported stdio runtime
kind when the provider fits the v1 manifest schema. Restart PLA HTTP only when
PLA's own runtime source code changes.
