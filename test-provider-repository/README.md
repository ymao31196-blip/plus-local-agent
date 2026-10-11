# PLA Test Repository

This is a test repository for the PLA component management system.

## Repository URL

`file:///D:/AI_Tools/plus-local-agent/test-provider-repository`

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
test-provider-repository/
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
