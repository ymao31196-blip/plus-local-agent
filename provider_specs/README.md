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

## Declarative routing metadata

External providers may declare capability-routing hints in their manifest without adding new top-level
MCP tools. Set the provider-level `routing_authority` to either `recommendation` or `preferred`,
then add a `routing` object to an individual `tool_overrides` entry.

Supported relations are:

- `preferred_over`: prefer this capability over another capability when the relation matches.
- `fallback_for`: use this capability only when the referenced source capability is unavailable.
- `supersedes`: request replacement semantics. External providers still cannot make this enforced;
  the effective routing mode is capped by the provider's authority.

Each relation names a `capability_id` and may include one `when` condition. Conditions currently
support a string argument with exactly one of `contains_any` or `equals_any`. Matching is
case-insensitive.

Example:

~~~json
{
  "routing_authority": "preferred",
  "tool_overrides": {
    "browser_click": {
      "routing": {
        "preferred_over": [
          {
            "capability_id": "computer.click",
            "when": {
              "argument": "app",
              "contains_any": ["edge", "chrome", "firefox"]
            }
          }
        ]
      }
    }
  }
}
~~~

Provider manifests cannot request `routing_authority: "enforced"`. Enforced routing remains a PLA
built-in policy authority. A third-party `supersedes` declaration under `preferred` therefore
resolves only to `specialized_preferred`, never `specialized_enforced`.

Routing is evaluated from the live Capability Registry snapshot. After a provider add/change/enable,
disable, or removal is applied through the normal provider rescan lifecycle, subsequent
`core.capability_route` calls see the new declaration immediately; no Routing-layer restart is
required.

Current providers:

- `browser`: Microsoft Playwright MCP `0.0.82`, installed into a provider-scoped npm prefix.
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
