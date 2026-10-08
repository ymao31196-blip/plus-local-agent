# PLA v2.0.1

PLA v2.0.1 is a maintenance release on top of v2.0.0. It packages the Source Layout Refactor (Phases 1–9), startup lifecycle repairs, and Execution Runner test isolation. The Control Plane / Execution Plane architecture introduced in v2.0.0 remains unchanged.

## Source layout and startup reliability

- Moved previously flat Python modules into focused packages under `src/`: agent, reasoning, execution, runtime, capabilities, routing, provider, browser, artifacts, tooling, host, project, transactions, hooks, diagnostics and MCP runtime integration.
- Kept `src/server.py` as the stable startup entry point.
- Corrected import paths, repository-root resolution, PowerShell launcher targets and subprocess Python paths after the package moves.
- Consolidated shared startup helpers, repaired Browser/HTTP/Execution Runner lifecycle references, and improved start/stop verification without changing the public MCP tool surface.
- Preserved the distinction between internal `src/provider/` management and concrete `providers/` servers. The third-party `mcp` Python package remains unshadowed.

## Execution Runner test isolation

- Changed the Windows Execution Runner mutex identity to derive from the canonical **durable state file path**. A production state file retains single-owner protection; isolated test instances use independent state files, authentication keys and Named Pipes.
- Added regression checks for same-state mutex exclusivity, different-state coexistence, and preservation of the live production Runner identity.
- Made tests that verify local temporary workspace, in-process streaming/cancellation and raw output counts explicitly select the in-process backend.
- Kept tests of real out-of-process Runner behavior on isolated Runner instances and verified pre-submit fallback separately.
- Kept the default generic `run_process` route on the production Execution Runner. No protocol, tool schema, authentication, root permission or confirmation-policy relaxation is part of this release.

## Validation

- Full repository regression with the production Execution Runner **running throughout**: **803 passed in 125.75s**, zero failures and zero skips.
- Windows startup lifecycle and complete 802-test regression were recorded during the earlier source-layout checkpoint, prior to the additional test-isolation regression check.
- Independent focused Runner / Canary / Candidate regression: **17 passed**.
- Source/test/provider Python compilation passed.
- The production Runner remained healthy with unchanged process identity throughout the test-isolation work.

## Operational notes

- Restart PLA after updating to load the new server version and runtime code. Stop active tasks safely before restarting.
- A release tag is a source-code checkpoint; it does not automatically restart an already running local installation.
- Current workspace checks remain wrapper-level controls; PLA does **not** claim a general OS sandbox.
- The Release version is `v2.0.1`; the FastMCP server version is `2.0.1`. Protocol compatibility is unchanged.

## References

- `docs/source_layout.md` — current package layout and compatibility contracts.
- `docs/startup_layout_regression.md` — source-refactor startup lifecycle validation.
- `docs/runner_test_isolation.md` — Runner and test workspace isolation contract.
- `docs/release_v2.0.0.md` — prior execution-plane release.
