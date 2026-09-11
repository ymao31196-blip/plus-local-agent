# Provider Manifest Schema (v1)

PLA v0.17 loads external MCP providers from `provider_manifests/*.json`.

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

## Selection

`PLA_EXTERNAL_PROVIDERS=*` enables every manifest with `autostart: true`.
A comma-separated value such as `markitdown,docx` explicitly selects providers.
An unknown provider id is rejected.

## Security constraints

- Runtime executables must be Python interpreters inside
  `.provider_envs/<provider_id>/`.
- Manifest paths must remain inside the PLA project root.
- Only allowlisted remote tools are registered when `tool_allowlist` is present.
- Tool overrides outside the allowlist are rejected.
- Artifact input/output policy remains enforced by the Capability Broker.
- `artifact_contract.policy` can declare bounded size, count, TTL and MIME limits.
- Provider policies may tighten limits but cannot exceed PLA hard caps.
- Credentials must not be stored in manifests.

## Adding a provider

1. Add `provider_specs/<provider_id>.txt`.
2. Add `provider_manifests/<provider_id>.json`.
3. Run `setup_providers.ps1`.
4. Restart PLA.

No Python runtime code change is required for another isolated Python stdio MCP
provider that fits the v1 manifest schema.
