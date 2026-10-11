#!/usr/bin/env python3
"""
Create a sample provider repository for testing PLA component management system.

Usage:
    python create_test_repository.py [output_directory]

This will create a complete test repository with one example provider.
"""

import argparse
import hashlib
import json
from pathlib import Path


def create_test_repository(output_dir: Path):
    """Create a complete test repository structure."""

    output_dir = output_dir.resolve()
    print(f"Creating test repository in: {output_dir}")

    # Create directory structure
    provider_dir = output_dir / "providers" / "test-echo-provider"
    version_dir = provider_dir / "versions" / "1.0.0"
    assets_dir = version_dir / "assets" / "providers"

    assets_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create repository index.json
    index = {
        "schema_version": 1,
        "repository_id": "pla-test-repo",
        "name": "PLA Test Repository",
        "description": "Test repository for PLA component management development",
        "url": f"file:///{output_dir.as_posix()}",
        "providers": [
            {
                "provider_id": "test-echo-provider",
                "name": "Test Echo Provider",
                "description": "Simple echo provider for testing component installation",
                "category": "general",
                "latest_version": "1.0.0",
                "repository_path": "providers/test-echo-provider"
            }
        ]
    }

    (output_dir / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False),
        encoding='utf-8'
    )
    print("[OK] Created index.json")

    # 2. Create provider metadata.json
    metadata = {
        "schema_version": 1,
        "provider_id": "test-echo-provider",
        "name": "Test Echo Provider",
        "description": "A simple test provider that echoes messages back. Used for testing the component management system.",
        "author": "PLA Development Team",
        "license": "MIT",
        "homepage": "https://github.com/example/test-echo-provider",
        "category": "general",
        "tags": ["test", "echo", "example"],
        "minimum_pla_version": "2.0.0",
        "repository_path": "providers/test-echo-provider/versions"
    }

    (provider_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding='utf-8'
    )
    print("[OK] Created metadata.json")

    # 3. Create versions index
    versions_index = {
        "schema_version": 1,
        "provider_id": "test-echo-provider",
        "versions": [
            {
                "version": "1.0.0",
                "release_date": "2024-10-10T00:00:00Z",
                "stability": "stable",
                "breaking_changes": False,
                "changelog": "Initial release - simple echo functionality",
                "minimum_pla_version": "2.0.0",
                "deprecated": False
            }
        ]
    }

    (provider_dir / "versions" / "index.json").write_text(
        json.dumps(versions_index, indent=2, ensure_ascii=False),
        encoding='utf-8'
    )
    print("[OK] Created versions/index.json")

    # 4. Create spec.json
    spec = {
        "schema_version": 1,
        "provider_id": "test-echo-provider",
        "version": "1.0.0",
        "runtime_kind": "isolated_python_stdio",
        "dependencies": {
            "python_version": "3.11.9",
            "system": [],
            "python_packages": [
                "mcp==2.2.0"
            ],
            "node_packages": [],
            "providers": []
        },
        "assets": {
            "providers": ["test_echo_provider.py"],
            "provider_specs": [],
            "provider_patches": []
        },
        "install_hooks": {
            "pre_install": None,
            "post_install": None
        },
        "uninstall_hooks": {
            "pre_uninstall": None,
            "post_uninstall": None
        }
    }

    (version_dir / "spec.json").write_text(
        json.dumps(spec, indent=2, ensure_ascii=False),
        encoding='utf-8'
    )
    print("[OK] Created spec.json")

    # 5. Create manifest.json
    manifest = {
        "schema_version": 1,
        "id": "test-echo-provider",
        "autostart": False,
        "mode": "auto",
        "routing_authority": "recommendation",
        "runtime": {
            "kind": "isolated_python_stdio",
            "python": ".provider_envs/test-echo-provider/Scripts/python.exe",
            "args": ["-m", "test_echo_provider"],
            "cwd": ".",
            "discovery_timeout_seconds": 10.0,
            "invoke_timeout_seconds": 60.0,
            "persistent_session": False
        },
        "tool_allowlist": ["echo_message", "echo_structured"],
        "tool_overrides": {}
    }

    (version_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding='utf-8'
    )
    print("[OK] Created manifest.json")

    # 6. Create provider Python code
    provider_code = '''"""Test Echo Provider for PLA Component Management Testing"""

from mcp.server.mcpserver import MCPServer
from typing import Dict, Any

mcp = MCPServer("Test Echo Provider")


@mcp.tool()
def echo_message(message: str) -> str:
    """Echo a message back to the caller.

    Args:
        message: The message to echo

    Returns:
        The echoed message with a prefix
    """
    return f"Echo: {message}"


@mcp.tool()
def echo_structured(data: Dict[str, Any]) -> Dict[str, Any]:
    """Echo structured data back to the caller.

    Args:
        data: A dictionary of data to echo

    Returns:
        The data wrapped in an echo structure
    """
    return {
        "echo": data,
        "provider": "test-echo-provider",
        "version": "1.0.0"
    }


if __name__ == "__main__":
    mcp.run()
'''

    (assets_dir / "test_echo_provider.py").write_text(
        provider_code,
        encoding='utf-8'
    )
    print("[OK] Created test_echo_provider.py")

    # 7. Generate checksums.json
    files_to_hash = {
        "manifest.json": version_dir / "manifest.json",
        "spec.json": version_dir / "spec.json",
        "assets/providers/test_echo_provider.py": assets_dir / "test_echo_provider.py"
    }

    checksums = {
        "schema_version": 1,
        "provider_id": "test-echo-provider",
        "version": "1.0.0",
        "files": {}
    }

    for rel_path, file_path in files_to_hash.items():
        content = file_path.read_bytes()
        sha256 = hashlib.sha256(content).hexdigest()
        checksums["files"][rel_path] = f"sha256:{sha256}"

    (version_dir / "checksums.json").write_text(
        json.dumps(checksums, indent=2, ensure_ascii=False),
        encoding='utf-8'
    )
    print("[OK] Created checksums.json")

    # 8. Create README
    readme = f'''# PLA Test Repository

This is a test repository for the PLA component management system.

## Repository URL

`file:///{output_dir.as_posix()}`

## Providers

### test-echo-provider

A simple echo provider for testing installation, upgrade, and uninstall workflows.

**Features:**
- `echo_message`: Echo a text message
- `echo_structured`: Echo structured JSON data

**Installation:**
1. Open PLA Desktop
2. Navigate to Component Management
3. Click "Add Repository"
4. Enter the repository URL above
5. Browse and install "Test Echo Provider"

## Testing Workflow

1. **Add Repository**: Add this repository to PLA Desktop
2. **Discover**: Browse available providers
3. **View Details**: Check provider metadata and versions
4. **Install**: Install test-echo-provider
5. **Health Check**: Verify installation health
6. **Enable**: Enable the provider in MCP settings
7. **Test**: Use the tools via ChatGPT
8. **Disable**: Disable the provider
9. **Uninstall**: Uninstall and verify cleanup

## Provider Structure

```
{output_dir.name}/
├── index.json                                    # Repository index
└── providers/
    └── test-echo-provider/
        ├── metadata.json                         # Provider metadata
        └── versions/
            ├── index.json                        # Version list
            └── 1.0.0/
                ├── manifest.json                 # Runtime manifest
                ├── spec.json                     # Installation spec
                ├── checksums.json                # File checksums
                └── assets/
                    └── providers/
                        └── test_echo_provider.py # Provider code
```
'''

    (output_dir / "README.md").write_text(readme, encoding='utf-8')
    print("[OK] Created README.md")

    print("\n" + "="*60)
    print("[SUCCESS] Test repository created successfully!")
    print("="*60)
    print(f"\nRepository location: {output_dir}")
    print(f"Repository URL: file:///{output_dir.as_posix()}")
    print(f"\nNext steps:")
    print(f"1. Copy the repository URL above")
    print(f"2. Open PLA Desktop")
    print(f"3. Go to Component Management -> Add Repository")
    print(f"4. Paste the URL and add the repository")
    print(f"5. Browse and install 'Test Echo Provider'")


def main():
    parser = argparse.ArgumentParser(
        description="Create a test provider repository for PLA"
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        default="./test-provider-repository",
        help="Output directory for the repository (default: ./test-provider-repository)"
    )

    args = parser.parse_args()
    output_dir = Path(args.output_dir)

    create_test_repository(output_dir)


if __name__ == "__main__":
    main()
