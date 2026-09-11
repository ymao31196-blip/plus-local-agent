# External Provider Environments

PLA external MCP providers run in isolated Python virtual environments under `.provider_envs/`.
They must not share PLA's main Python environment because MCP SDK requirements can conflict.

Provider installation is driven by `provider_specs/*.txt`. Provider runtime/capability policy is driven
by `provider_manifests/*.json`. Adding an isolated Python MCP provider normally requires:

1. Add `provider_specs/<provider_id>.txt` with pinned dependencies.
2. Add `provider_manifests/<provider_id>.json` with runtime, tool allowlist and policy overrides.
3. Run `setup_providers.ps1`.
4. Restart PLA. If the manifest has `"autostart": true`, the normal HTTP launcher will pick it up automatically.

The runtime never installs provider dependencies automatically.

Current providers:

- `markitdown`: Microsoft MarkItDown MCP, pinned to the legacy MCP SDK it requires.
- `docx`: DOCX MCP Server, pinned to MCP 1.30.0 because version 0.7.4 still imports the MCP 1.x FastMCP API.
- `pdf`: PDF MCP Server 0.1.2, isolated on FastMCP 4 / MCP 2 and restricted to the reviewed `add_text_watermark_direct` capability.
