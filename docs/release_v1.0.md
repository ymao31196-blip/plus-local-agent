# PLA v1.0 Freeze Checklist

This document records the release-candidate boundary. It is intentionally
separate from the historical v0.x handoff documents.

## Required before commit/tag

- [x] Runtime/version set to `1.0.0`.
- [x] Workspace contains only the reproducible manual E2E fixture.
- [x] No tracked plaintext credentials or model API keys were found in the
      release audit. The protected tunnel config contains only a file reference;
      `start_tunnel.ps1` supplies the runtime credential reference explicitly.
- [x] Main and all three provider environments pass `pip check`.
- [x] Provider Doctor reports all reviewed providers healthy with no version
      drift: `docx`, `markitdown`, and `pdf` (3/3).
- [x] Full `pytest -q` regression passes: **335 passed in 80.23 s**.
- [x] Project and test Python source compiles: **70 files** checked.
- [x] Public MCP schema contains broker/governance tools, not raw downstream
      provider tools. Final source-level catalog count: **48** public tools.
- [x] Real cross-provider E2E passes after the final source changes:
      Markdown→DOCX→MarkItDown and real PDF→PDF watermark→MarkItDown.
- [x] Final E2E verifies valid provenance, changed PDF SHA, preserved readable
      article content, and no `.capability_io` path leakage.
- [x] Artifact GC was not executed destructively as part of release validation.
      Temporary final-E2E artifacts were explicitly revoked by their ids.
- [x] Workspace/runtime test artifacts were cleaned after validation.
- [x] Git diff/status reviewed; the remaining dirty set is the intended
      accumulated v0.7–v1.0 source/test/documentation release candidate.
- [x] Third-party license note is included for the PDF provider. In particular,
      local package metadata reports PyMuPDF as AGPL-3.0 / Artifex commercial
      dual licensed; bundled redistribution requires separate review.
- [ ] Git commit/tag performed only after explicit repository-write
      authorization.

## Manual Agent-loop fixture

`workspace/agent_test/calculator.py` is intentionally restored to subtraction.
An explicit release check of `pytest -q workspace/agent_test` produced the
expected single failure (`add(2, 3) == -1`, expected 5). Normal project
`pytest -q` remains isolated to `tests/`.

## Portability note

`start_http.ps1` and `setup_providers.ps1` accept `PLA_PYTHON`.
`start_tunnel.ps1` accepts `PLA_TUNNEL_CLIENT` and
`PLA_TUNNEL_CREDENTIAL` and otherwise derives the sibling tunnel-client
location from the PLA project directory.

`config/tunnel.yaml` is deliberately operator-owned/read-only through PLA
self-maintenance. It currently contains an operator-specific credential file
reference; the startup script passes its resolved credential reference as a CLI
override. PLA did not bypass the protected config boundary during freeze work.

## Freeze rule

After the v1.0.0 release commit, changes should be classified as:

- patch release: bug/security/reliability/documentation fixes
- minor release: new reviewed provider capabilities or backward-compatible
  runtime features
- major release: public broker contract, Artifact identity semantics, or
  security-boundary incompatibilities

Do not resume version-number-driven feature expansion without a concrete user
need or a failed acceptance scenario.
