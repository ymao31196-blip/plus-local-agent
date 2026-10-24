# PLA Desktop v1.0.0 — third-party provenance and redistribution audit

Date: 2026-10-25. Assessment: **source/packaging preflight complete; final installer and legal review outstanding**.
This is a technical license-inventory check, not legal advice or proof that every source line has an independently established origin.

## Scope and method

- Reviewed the public source's tracked-file inventory, bundled patch, reviewed
  Wheel, dependency specifications, packaging license collector and previous
  RC.18 release notice output.
- Checked the actual WPS Skills upstream `LICENSE` at its explicitly pinned
  `lc2panda/wps-skills` revision, not merely its current default branch.
- Inspected on-demand Provider dependencies separately from binaries shipped in
  the base NSIS installer. A spec that installs a dependency at runtime is not
  evidence that its code is bundled into the base installer.
- Reviewed a narrow current-file secret-pattern scan plus the names of files
  found in Git history. Scans are heuristic and cannot certify that no secret
  or confidential material was ever committed.

## Verified findings

| Item | Evidence | Disposition |
| --- | --- | --- |
| PLA original implementation | Repository root `LICENSE`: Apache License 2.0 standard text | Owner's chosen project license; retain original third-party rights |
| Independent Skill Library | Separate source commit `13cc22435152d475e72e4cfe1aa0dc4e0657d16f`; bundled Wheel SHA-256 `869bd376af7c729172283017b36c694ad0fc128c5d54ffa7a897ce4b4d3f1eb0` includes `License-Expression: Apache-2.0` and complete text | Verified owned component; separate test suite: 76 passed, 2 skipped |
| WPS upstream | `https://github.com/lc2panda/wps-skills` at `a82533662268b3245f93d8685bc45dffede048b6`: MIT License, copyright 2026 lc2panda | Origin and exact MIT text now preserved as `provider_patches/wps-office/UPSTREAM-LICENSE.txt`; keep MIT rights/credit on copied snippets |
| WPS delivery | The only first-party stored WPS derivative is `provider_patches/wps-office/windows-com-compat.patch`; build recursively copies `provider_patches` with new license file; upstream program is cloned/installed separately | Patch and text can be distributed together; do not relabel upstream as PLA Apache-2.0 source |
| Optional Office Enhancement | `provider_specs/office-enhancement.txt` pins `PyMuPDF==1.28.2`; Artifex lists AGPL-3.0 or commercial terms | **Review on-demand install, network use and downstream redistribution separately.** This dependency is not in the base NSIS spec; do not assert it automatically becomes Apache-licensed |
| Base installer package license processing | `desktop/packaging/licenses.py` gathers installed distribution notices and Cargo dependency texts, rejects missing license texts for relevant target packages, preserves required MPL source archives, and uses SHA-256-pinned supplemental license assets | Mechanism present; only the **final build output** proves which actual dependencies were shipped |
| Existing RC.18 notices | Local `dist/desktop-v1/third-party-licenses/runtime` has 871 files grouped under 515 immediate directories, from earlier release build | Historical supporting evidence only; it cannot qualify v1.0.0 |
| Public source inventory | 503 currently tracked files at scan time; one retained third-party patch under `provider_patches/`; no obvious sensitive credential filenames in tracked files or historical path-name scan | Not an exhaustive audit of code origin, all Git history content, vendored or generated binaries |
| Secret-pattern scan | Four candidate files in tests/verification, five matches with explicit test/fixture contexts, no raw values printed | No obvious live secret from this scan; still require final artifact and Git commit credential review |

## Remaining review and release gates

1. Confirm first-party code ownership and any outside contributions or copied
   algorithm/code fragments under `src/`, `providers/` and `desktop/`.
   Filename/regex inspection alone cannot prove authorship.
2. Run the full Windows CI packaging process on a **clean committed candidate**
   (not the current RC.18 `source_dirty=true` local manifest).
   Inspect license inventories, missing-notice failures, MPL source archives,
   WPS patch/notice copy and the exact `third-party-licenses` delivery.
3. Keep PyMuPDF/AGPL and any other optional strong-copyleft dependency explicit
   in third-party documentation; if producing a combined redistributed package,
   obtain a proper license compatibility review rather than assuming subprocess
   isolation resolves the issue.
4. Complete clean-machine installation, upgrade, configuration preservation,
   restart and remote ChatGPT end-to-end tests before stable v1.0.0 tagging.
5. Do not change other authors' license texts. In particular, keep the exact
   MIT WPS upstream notice with the patch and the Apache-2.0 Skill Library
   license inside its bundled Wheel.

## Follow-up evidence

- GPL/AGPL source: https://pymupdf.io/licensing
- Pinned WPS license: https://github.com/lc2panda/wps-skills/blob/a82533662268b3245f93d8685bc45dffede048b6/LICENSE
- Build license collection: `desktop/packaging/licenses.py`
- Existing dependency-notice overview: `docs/desktop-v1-third-party-notices.md`

**Decision:** No newly verified license issue requires changing PLA's first-party
Apache-2.0 license. The optional PyMuPDF terms are a documented downstream
integration condition. v1.0.0 remains **not qualified** until the final built
artifact and operational release checks pass.
