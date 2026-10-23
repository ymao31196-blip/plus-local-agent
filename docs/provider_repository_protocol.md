# Provider Repository Protocol v1

## Overview

Provider Repository 是一个标准化的组件仓库协议，用于发现、下载和安装 PLA MCP Providers。

## Repository Structure

```
repository-root/
├── index.json                    # Repository metadata and provider list
└── providers/
    ├── {provider-id}/
    │   ├── metadata.json         # Provider general metadata
    │   └── versions/
    │       ├── index.json        # Available versions list
    │       └── {version}/
    │           ├── manifest.json # Runtime manifest for this version
    │           ├── spec.json     # Installation specification
    │           ├── assets/       # Provider resources
    │           │   ├── providers/
    │           │   ├── provider_specs/
    │           │   └── provider_patches/
    │           └── checksums.json # SHA-256 checksums
    └── ...
```

## File Specifications

### index.json (Repository Root)

```json
{
  "schema_version": 1,
  "repository_id": "official",
  "name": "PLA Official Repository",
  "description": "Official PLA provider repository",
  "url": "https://example.com/pla-providers",
  "providers": [
    {
      "provider_id": "example-provider",
      "name": "Example Provider",
      "description": "Short description",
      "category": "automation",
      "latest_version": "1.0.0",
      "repository_path": "providers/example-provider"
    }
  ]
}
```

### metadata.json (Provider Level)

```json
{
  "schema_version": 1,
  "provider_id": "example-provider",
  "name": "Example Provider",
  "description": "Detailed description of the provider",
  "author": "PLA Team",
  "license": "MIT",
  "homepage": "https://github.com/example/provider",
  "category": "automation",
  "tags": ["automation", "workflow"],
  "minimum_pla_version": "2.0.0",
  "repository_path": "providers/example-provider/versions"
}
```

### versions/index.json

```json
{
  "schema_version": 1,
  "provider_id": "example-provider",
  "versions": [
    {
      "version": "1.0.0",
      "release_date": "2024-10-10T00:00:00Z",
      "stability": "stable",
      "breaking_changes": false,
      "changelog": "Initial release",
      "minimum_pla_version": "2.0.0",
      "deprecated": false
    }
  ]
}
```

### {version}/spec.json (Installation Specification)

```json
{
  "schema_version": 1,
  "provider_id": "example-provider",
  "version": "1.0.0",
  "runtime_kind": "isolated_python_stdio",
  "dependencies": {
    "python_version": "3.11.9",
    "system": [],
    "python_packages": [
      "package-name==1.0.0"
    ],
    "node_packages": [],
    "providers": []
  },
  "assets": {
    "providers": ["example_provider.py"],
    "provider_specs": ["requirements.txt"],
    "provider_patches": []
  },
  "install_hooks": {
    "pre_install": null,
    "post_install": null
  },
  "uninstall_hooks": {
    "pre_uninstall": null,
    "post_uninstall": null
  }
}
```

### {version}/manifest.json (Runtime Manifest Template)

Standard PLA provider manifest with placeholders:

```json
{
  "schema_version": 1,
  "id": "example-provider",
  "autostart": false,
  "mode": "auto",
  "routing_authority": "recommendation",
  "runtime": {
    "kind": "isolated_python_stdio",
    "python": ".provider_envs/example-provider/Scripts/python.exe",
    "args": ["-m", "example_provider"],
    "cwd": ".",
    "discovery_timeout_seconds": 10.0,
    "invoke_timeout_seconds": 60.0,
    "persistent_session": false
  },
  "tool_allowlist": null,
  "tool_overrides": {}
}
```

### {version}/checksums.json

```json
{
  "schema_version": 1,
  "provider_id": "example-provider",
  "version": "1.0.0",
  "files": {
    "manifest.json": "sha256:...",
    "spec.json": "sha256:...",
    "assets/providers/example_provider.py": "sha256:...",
    "assets/provider_specs/requirements.txt": "sha256:..."
  }
}
```

## Repository Types

### 1. Local File Repository
- `file:///path/to/repository`
- Direct filesystem access
- No signature verification required
- For development and testing

### 2. HTTP Repository
- `https://example.com/pla-providers`
- Standard HTTP(S) access
- Supports caching
- Signature verification recommended

### 3. Git Repository
- `git+https://github.com/org/repo`
- Clone/pull to local cache
- Commit hash verification
- For source-based distributions

## Installation Flow

1. **Discovery**: Fetch repository index.json
2. **Select**: User chooses provider and version
3. **Download**: Fetch version-specific files
4. **Verify**: Check checksums
5. **Prepare**: Create isolated environment
6. **Install**: Install dependencies according to spec.json
7. **Deploy**: Copy assets to component directory
8. **Register**: Generate final manifest and register with Runtime
9. **Verify**: Test provider discovery and basic invocation

## Version Constraints

- Semantic versioning (MAJOR.MINOR.PATCH)
- Version comparison: exact match, minimum version, range
- Breaking change indicators
- Deprecation warnings

## Security Considerations

1. **Checksum Verification**: All files must match checksums.json
2. **HTTPS Only**: Production repositories must use HTTPS
3. **Path Validation**: All paths must be relative and validated
4. **Dependency Isolation**: Dependencies are installed into private version directories. This is not an operating-system sandbox; enabled provider code runs as the current user.
5. **User Confirmation**: Installation requires explicit user approval
6. **Source Review**: Repository maintainers review all provider code

## Error Handling

- Network failures: Report and preserve existing data; retry is explicit
- Checksum mismatches: Abort installation
- Missing dependencies: Report and block installation
- Unsupported runtime kinds, hooks and system dependency requests: Reject; do not silently execute
- Partial installations: Automatic rollback

## Future Extensions

- Digital signatures (GPG/Authenticode)
- Delta updates for large providers
- Multi-repository priority and fallback
- Provider ratings and reviews
- Automatic security scanning


## RC.10 implementation contract

The desktop has one **组件与 MCP 插件** navigation entry with runtime management,
repositories, marketplace and installed-component tabs. Existing manifests,
full capability schemas and hot-plug controls remain in runtime management.

Sources currently supported are local `file:///` URLs and HTTPS repositories.
Git repositories and signed repository indexes are not implemented. Repository
checksums detect inconsistent bytes; they do not authenticate an untrusted
publisher. The user must review the selected source and provider code.

Preview downloads and verifies raw spec/manifest bytes and every asset, stores
an immutable private plan and returns a SHA-256 digest. Confirmed install/upgrade
requests must match the operation, ID, requested version and URL of that plan.
Uninstall also requires `expected_sha256`. Existing installation state is
rechecked at execution. A successful request returns `started: true` and a job;
`installation_status` reports `running`, `completed` or `failed` for repository
jobs. A launched job does not mean installation succeeded.

Python uses the private verified uv installer and managed Python 3.11.9.
PyPI and npm requirements must use exact pins. npm installation disables lifecycle
scripts. Repository system/provider dependencies and executable install hooks
are rejected. Node providers use the bundled Node executable and a private module
resolution loader. No general Shell RPC is exposed.

Artifacts are placed under `repository-assets/<id>/<version>` and isolated
environments under `.provider_envs/<id>/<version>`. Upgrade records ownership of
previous versions. Uninstall verifies the installed manifest and all registered
assets first; user-modified assets block removal, and unrelated files are retained.
A reversible staging transaction precedes state commit. Operating-system locks
can defer cleanup into a uniquely owned transaction directory and are logged.
Installation does not enable a provider. Disable an enabled provider before
upgrading or removing it, then enable/reconnect after installation.

Health checks currently prove installation integrity, with
`scope: installation_integrity` and `mcp_connection_verified: false`.
Live Runtime connection and full tool schemas are shown in runtime management.
