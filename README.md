# plus-local-agent

**PLA v1.0.0 — ChatGPT Local Capability Runtime**

PLA is a local MCP runtime designed around one architectural rule:

> ChatGPT is the Agent Brain. PLA owns local capability execution, artifacts,
> provider lifecycle, persistent tasks, and safety boundaries.

PLA does not require an OpenAI model API key and does not use a second local
planner as its production control loop.

## Architecture

```text
User
  ↓
ChatGPT
  │ stable MCP broker tools
  ▼
PLA Capability Runtime
  ├─ Capability Registry / Broker
  ├─ Artifact Plane
  ├─ Provider Lifecycle / Doctor
  ├─ Task Runtime
  ├─ Local Executor
  └─ External MCP Providers
       ├─ markitdown
       ├─ docx
       └─ pdf
```

External providers are declared in `provider_manifests/*.json`, installed in
isolated `.provider_envs/<provider_id>/` environments, and exposed through the
stable `capability_search → capability_describe → capability_invoke` interface.
Raw downstream tool catalogs are not copied into ChatGPT's MCP schema.

## Current reviewed providers

- `markitdown.convert_to_markdown`: read document artifacts as Markdown.
- `docx.create_from_markdown`: create one DOCX artifact from Markdown.
- `pdf.add_text_watermark_direct`: create one watermarked PDF artifact.

Provider manifests apply tool allowlists, confirmation policy, artifact MIME and
resource limits, and managed output paths.

## Installation

Use Python 3.11 for the main runtime.

```powershell
python -m pip install -r requirements-core.txt
python -m pip install -r requirements-documents.txt
`

For development/testing:

```powershell
python -m pip install -r requirements-dev.txt
`

Set `PLA_PYTHON` when the PLA interpreter is not at the default Windows
Miniconda location.

Install isolated provider environments:

```powershell
.\setup_providers.ps1
`

## Start / stop

Start HTTP runtime and Secure MCP Tunnel:

```powershell
.\start_all.ps1
`

Restart only the PLA HTTP runtime after source changes:

```powershell
.\restart_pla.ps1
`

Stop the owned tunnel and HTTP listener:

```powershell
.\stop_all.ps1
`

Optional path overrides:

- `PLA_PYTHON`: main PLA Python interpreter.
- `PLA_TUNNEL_CLIENT`: tunnel-client executable.
- `PLA_TUNNEL_CREDENTIAL`: tunnel credential file.
- `PLA_EXTERNAL_PROVIDERS`: comma-separated provider ids, or `*` for
  autostart manifests.

The protected `config/tunnel.yaml` is operator-owned. Runtime credentials are
passed by `start_tunnel.ps1`; secrets must never be stored in provider
manifests or source code.

## Verification

Automated regression:

```powershell
pytest -q
```

Provider health and pinned-version drift are available through
`provider_doctor`. Artifact governance is available through
`artifact_verify` and dry-run-first `artifact_gc`.

The reproducible human Agent-loop fixture is
`workspace/agent_test/calculator.py`; it is intentionally broken until ChatGPT
repairs it during the acceptance exercise. The project pytest configuration
collects only `tests/`.

## Security model

PLA uses named-root path validation, protected paths, program/cmdlet allowlists,
`shell=False`, optimistic SHA-256 write preconditions, transactional
multi-file changesets, immutable artifacts, provider tool allowlists, managed
provider output paths, MIME/content checks, resource limits, provenance
verification, and explicit confirmation gates.

PLA is **not an OS sandbox**. An allowed local child program runs with the host
account's filesystem authority. Provider packages are third-party code and must
remain isolated and reviewed before being allowlisted.

## Important known limitations

- Cancellation guarantees the owned direct child only, not an arbitrary process
  tree.
- ChangeSet rollback is transactional at the application layer but not
  power-loss/crash atomic at the filesystem level.
- Provider health probes can rediscover/recover providers; PLA does not
  automatically replay failed business calls.
- Artifact provenance is an integrity/audit mechanism, not a cryptographic
  signature against an attacker who can rewrite the entire local store.
- `config/tunnel.yaml` contains operator-specific tunnel configuration and is
  deliberately read-only through PLA self-maintenance.
- The reviewed PDF provider depends on PyMuPDF, whose package metadata reports
  AGPL-3.0 / Artifex commercial dual licensing; bundled redistribution requires
  a separate license review.

## Documentation

- `docs/v1_overview.md` — v1 architecture and acceptance boundary.
- `docs/architecture.md` — detailed Agent Runtime / local executor history.
- `docs/artifact_security.md` — Artifact Plane security and governance.
- `docs/provider_lifecycle.md` — provider lifecycle and Doctor semantics.
- `docs/v020_pdf_provider.md` — final real PDF-provider E2E before v1 freeze.
- `docs/chatgpt_e2e.md` — human Agent-loop acceptance procedures.
