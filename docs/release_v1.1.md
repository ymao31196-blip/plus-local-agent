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
- Manifest-backed providers support confirmation-gated hot-plug rescan, reload,
  temporary enable, and temporary disable without restarting PLA HTTP.

## Required before commit/tag

- [x] Runtime/version set to `1.1.0`.
- [x] The pre-hot-plug release commit was
      `4487d326f8afae7450c374c1e3f3db3f6dafb8f6`; no tag or push had occurred,
      so the hot-plug runtime was added before the public v1.1.0 release boundary.
- [x] Provider Doctor live probe reports **6/6 healthy**, **0 degraded**:
      `docx`, `markitdown`, `pdf`, `software-migration`,
      `windows-management`, and `winget`.
- [x] No pinned Python-provider version drift detected.
- [x] Main environment and all five Python provider environments pass
      `pip check`.
- [x] Project/test Python source compiles: **89 files**, **0 errors**.
- [x] Public MCP schema count after controlled release operations: **55 tools**.
- [x] Capability Registry count: **52 capabilities**.
- [x] Capability Registry includes **5 core transaction capabilities**,
      **2 confirmation-gated core release capabilities**, and
      **5 runtime provider hot-plug capabilities**.
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
- [x] Controlled release Git operations are implemented and locally E2E-tested:
      `git_tag` creates only a new lightweight tag at the exact clean expected
      HEAD; `git_push` requires `confirmation="PUSH"`, forbids force, pushes
      branch + explicit tags atomically to an existing named remote, and verifies
      resulting remote refs.
- [x] Final v1.1.0 full `pytest -q` regression passes after hot-plug integration:
      **413 passed in 84.68 s**.
- [x] Cold stop/start E2E passes after hot-plug integration. `stop_all.ps1`
      stopped Tunnel PID 3104, HTTP PID 25460, and Broker PID 24104;
      `start_all.ps1` restored Tunnel PID 884, HTTP PID 25640, and Broker PID
      19536. Post-start Provider Doctor again reported 6/6 healthy, all 5
      `runtime.provider_*` capabilities were present, and the Capability Registry
      returned all 50 capabilities.
- [x] Live provider hot-plug E2E passed without HTTP restart: a temporary
      read-only WinGet-backed provider was added by `provider_rescan`, disabled,
      re-enabled, manifest-edited and `provider_reload`ed, then removed from disk
      and rescanned away. HTTP PID remained **25460** throughout the hot-plug sequence.
- [x] Final Git diff/status reviewed. The pre-commit worktree contains only the
      intended hot-plug runtime, manager/registry support, release/test updates,
      and provider documentation; the temporary live-E2E manifest is absent.
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