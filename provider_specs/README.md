# External Provider Environments

PLA external MCP providers use reviewed, provider-scoped environments under `.provider_envs/`.
Python providers use isolated virtual environments so MCP SDK requirements cannot conflict with PLA.
Node providers use a provider-scoped npm prefix; their packages are never added to PLA's generic
`run_process` allowlist.

Provider installation is driven by pinned specs:
- `provider_specs/<provider_id>.txt` for Python packages.
- `provider_specs/<provider_id>.npm.txt` for Node packages.

Provider runtime/capability policy remains declarative under `provider_manifests/*.json`.
Adding a reviewed provider normally requires:

1. Add the pinned dependency spec for its runtime.
2. Add `provider_manifests/<provider_id>.json` with runtime, tool allowlist and policy overrides.
3. Run `setup_providers.ps1`.
4. Restart PLA or hot-rescan the provider. If the manifest has `"autostart": true`, the normal
   HTTP launcher will pick it up automatically.

The runtime never installs provider dependencies automatically.

Current providers:

- `browser`: Microsoft Playwright MCP `0.0.80`, installed into a provider-scoped npm prefix.
  The Browser Runtime uses a PLA-managed browser profile and exposes only reviewed semantic tools.
- `computer`: PLA's Windows Computer Use provider. It wraps Microsoft winapp CLI `0.5.0`
  from a provider-scoped npm prefix and exposes a semantic-first UI Automation surface.
  Raw coordinates, system shortcuts, touch/pen, recording, arbitrary command execution, and
  full-screen capture are intentionally excluded from the v1.4 surface.
- `markitdown`: Microsoft MarkItDown MCP, pinned to the legacy MCP SDK it requires.
- `docx`: DOCX MCP Server, pinned to MCP 1.30.0 because version 0.7.4 still imports the MCP 1.x FastMCP API.
- `pdf`: PDF MCP Server 0.1.2, isolated on FastMCP 4 / MCP 2 and restricted to the reviewed `add_text_watermark_direct` capability.
- `windows-management`: Windows Management MCP Server 0.3.1, pinned to MCP
  1.30.0 / FastMCP 3.4.7 because the upstream 0.3.1 server still imports the
  MCP 1.x `mcp.server.fastmcp` API; only a reviewed read-only observation
  allowlist is exposed by PLA.
- `software-migration`: PLA's migration-specific provider on FastMCP 4 / MCP 2.
  Its first surface is intentionally read-only: registry-based migration
  assessment plus a non-executing WinGet `--location` reinstall preview.
