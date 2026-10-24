# PLA User-Controlled Executable Grants (Phase 1)

Status: implementation committed locally at aa3d3e9005800ff71565a9bf172be5a43bc800bd and tested against the packaged Desktop Runtime. Not pushed or released; the existing local RC.18 installer is not a clean v1.0.0 release.

## User behavior

PLA Desktop → Settings → Executable Permissions. Enter the **absolute path**
of an already installed Windows `.exe` to grant it. The list supports grant,
disable, re-enable and revoke. Every permission-changing operation requires
an explicit confirmation in the local Desktop UI. Agents cannot call this
management command through the MCP tool registry.

The pre-existing built-in allowlist is unchanged. A custom grant matches the
**canonical full path** only, not the executable's basename or a directory.
Invalid/missing configuration fails closed. Grant changes apply to new process
requests without restarting the Runtime; a running process is not killed when
a grant is revoked. Up to 64 custom executables can be recorded.

Shell interpreters (PowerShell, pwsh, cmd, wscript, cscript, mshta) remain
blocked from *custom* grants in Phase 1. The existing structured PowerShell,
Git and root safeguards remain separately enforced. Session executables have
not been opened to custom grants.

## Storage and execution

In Desktop mode the private manager writes
`<PLA_DATA_ROOT>/config/program_permissions.json` atomically using a
SHA-256 optimistic-concurrency precondition. It communicates the location
to Runtime and Runner through `PLA_PROGRAM_PERMISSIONS_FILE`, read for each
authorization. The private UI uses the existing Tauri → manager RPC.

In developer source mode the default file is
`config/program_permissions.local.json`, which is protected from ordinary
MCP file access through the dedicated `pla` root policy.

Both the MCP entry point and the separate production runner recheck grants.
The execution tool continues to require an allowed working-directory root
and to use its existing semantic execution policy.

## Limits and security boundaries

This is a **user-approved program-launch allowlist**, not an operating-system
sandbox. An authorized executable inherits the permissions of the Windows
account and may interact with paths outside the requested PLA workspace.
PLA's pre-existing Python/script execution allowance likewise means that
workspaces are not a confinement mechanism for arbitrary native code.

Phase 1 does not offer arbitrary shell access, per-argument control, trust
attestation, process isolation, or an unattended advanced mode. Do not infer
those from the new UI. Malicious programs, scripts, unexpected executable
replacement and remote prompt injection remain security concerns.

## Validation before release

- New grant, disable, revoke, stale SHA and malformed policy tests
- Read-only MCP versus private Desktop mutation boundary
- Existing workspace, Runner, Desktop manager and UI release contracts
- JS syntax check where Node is available
- Local source-run verification, then a fresh Desktop build and manual
  install/launch test before any release

Do not ship without manually exercising the real Desktop settings page and
a new authorized executable via the production Runner.
