# PLA v1.5.1

PLA v1.5.1 separates customer workspace authorization from the product source repository.

## Runtime Workspace Registry

The built-in `pla` and `workspace` roots remain part of the product runtime. Additional
machine-specific roots are loaded from the Git-ignored `config/workspaces.local.yaml`.
`config/workspaces.example.yaml` documents the supported schema.

The registry is read dynamically when customer roots are resolved, so changing a customer root
does not require editing source files or restarting PLA.

## Stable control surface

The core capability registry adds:

- `core.workspace_roots_get`
- `core.workspace_root_upsert`
- `core.workspace_root_remove`

Status is read-only. Upsert and remove require explicit `INVOKE` confirmation and the current
workspace-config SHA-256, preventing stale callers from overwriting concurrent local changes.

## Security boundaries

Configured customer roots cannot overlap, contain, or sit inside the PLA source tree. This prevents
an alternate root name from bypassing `pla` private-path restrictions. The local registry file is
itself protected from ordinary `pla` root file operations.

Malformed customer registry configuration does not prevent built-in `pla` or `workspace` access,
so the runtime retains a recovery path.

PLA release capabilities `core.git_tag` and `core.git_push` no longer accept a selectable root and
are always executed against `pla`. Ordinary Git tools remain available for user repositories inside
authorized customer roots.

## Migration

Existing source-level customer roots such as hard-coded Desktop or project paths should be removed
from source and represented in `config/workspaces.local.yaml` instead. Existing machine-specific
access on the development/test machine can therefore coexist with a clean PLA source checkout.

## Validation

The v1.5.1 regression suite covers dynamic registry loading, hot configuration changes, optimistic
concurrency, protected local configuration, source-tree overlap rejection, dynamic root schemas,
Broker confirmation gates, and release-domain isolation. Final source regression: **561 passed in
92.66 s**.

A loaded-runtime E2E after `runtime.restart_http` verified that the live Core catalog exposes all
three workspace-registry capabilities; the machine-local registry returned only `desktop` and
`rerun_thesis`; a temporary `e2e_workspace` root was added with `INVOKE`, used immediately without
another restart to write a probe file, and then removed again. PLA source Git status remained the
same 16 v1.5.1 product changes throughout, and the isolated test directory was deleted after
verification.
