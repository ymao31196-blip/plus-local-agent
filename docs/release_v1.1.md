# PLA v1.1.0 Freeze Checklist

This document records the v1.1.0 release-candidate boundary after the v1.0.0
baseline and the governed-provider / software-migration work.

## Release scope

v1.1.0 is a backward-compatible minor release. It adds reviewed runtime
capabilities without changing the production decision boundary:

- ChatGPT remains the agent brain and sole production planner.
- PLA remains the local capability runtime and governance layer.
- The stable ChatGPT-facing surface remains
  `capability_search → capability_describe → capability_invoke`.
- Durable transaction governance is also exposed through the built-in
  `core.transaction_*` capabilities.
- Windows elevation is mediated through a dedicated Interactive Elevation Broker.
- WinGet, Windows Management, and Software Migration providers are reviewed and
  manifest-governed.

## Required before commit/tag

- [x] Runtime/version set to `1.1.0`.
- [x] Repository was clean before release-only edits; baseline HEAD was
      `ec72f7b023d529ae6e09803529f8562519430c45`.
- [x] Provider Doctor live probe reports **6/6 healthy**, **0 degraded**:
      `docx`, `markitdown`, `pdf`, `software-migration`,
      `windows-management`, and `winget`.
- [x] No pinned Python-provider version drift detected.
- [x] Main environment and all five Python provider environments pass
      `pip check`.
- [x] Project/test Python source compiles: **87 files**, **0 errors**.
- [x] Public MCP schema count: **53 tools**.
- [x] Capability Registry count: **45 capabilities**.
- [x] Capability Registry includes **5 core transaction capabilities**.
- [x] Software Migration provider exposes **11 reviewed capabilities**.
- [x] Windows Management exposes **24 allowlisted capabilities**.
- [x] WinGet exposes **2 allowlisted capabilities**.
- [x] Real Zotero migration E2E completed through durable transaction +
      Interactive Elevation Broker + UAC + WinGet installation:
      Zotero 10.0.2 ended at `D:\Apps\Zotero`, old C-drive program directory
      was absent, and user-data manifest / `zotero.sqlite` SHA-256 matched the
      verified backup before commit.
- [x] Structured Git staging supports explicit modified files and non-ignored
      new regular files while preserving SHA-256, expected-HEAD, clean-index,
      exact-path, raw-byte, and compare-and-swap constraints.
- [x] Final v1.1.0 full `pytest -q` regression passes after version bump: **404 passed in 89.81 s**.
- [x] Cold stop/start E2E passes. `stop_all.ps1` stopped Tunnel PID 9108, HTTP PID 22984, and Broker PID 13716; `start_all.ps1` restored Tunnel PID 3104, HTTP PID 24952, and Broker PID 24104. Post-start Provider Doctor again reported 6/6 healthy, the Broker reported `Session 1 / WinSta0 / Default / ready`, and the Capability Registry returned all 45 capabilities.
- [x] Final Git diff/status reviewed; the release worktree contains only the version bump, freeze test update, `CHANGELOG.md`, and this release note before the release commit.
- [ ] Annotated/lightweight release tag created only after the release commit.
- [ ] Push performed only after repository-write/network action is explicitly
      authorized and supported by the controlled Git boundary.

## Security boundary

v1.1.0 does not convert PLA into an OS sandbox. Reviewed child processes inherit
host-user authority unless a narrowly reviewed action is elevated.

The release preserves these boundaries:

- named roots and protected paths;
- program/provider allowlists;
- manifest-level risk, confirmation, and transaction policies;
- no arbitrary `runas` surface;
- Interactive Elevation Broker accepts only reviewed request shapes;
- runtime state, provider environments, workspace output, credentials, and logs
  remain ignored;
- binary provider handoffs continue through the Artifact Plane.

## Third-party note

The v1.0 PDF licensing note still applies. Local package metadata reports
PyMuPDF under AGPL-3.0 / Artifex commercial dual licensing; redistribution
requires separate review.

## Freeze rule

After the v1.1.0 release commit, further changes should be classified as:

- patch: bug, security, reliability, or documentation fixes;
- minor: new reviewed providers or backward-compatible runtime capabilities;
- major: incompatible stable broker contracts, artifact identity semantics, or
  security-boundary changes.

Do not expand the runtime solely to increase the version number; require a
concrete user need or failed acceptance scenario.