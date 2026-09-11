# PLA v1.0 Architecture Overview

## Release boundary

PLA v1.0 freezes the production architecture as:

```text
ChatGPT Agent Brain
        │
        │ MCP
        ▼
PLA Local Capability Runtime
        ├── Stable broker: search / describe / invoke
        ├── Local execution boundary
        ├── Durable Task Store
        ├── Artifact Plane
        ├── Provider Manifest + Lifecycle
        ├── Security / policy enforcement
        └── Isolated external MCP providers
```

There is one production decision-maker: ChatGPT. PLA does not plan multi-step
business workflows, choose the next capability, or autonomously chain providers.
Cross-provider pipelines are created by consecutive ChatGPT decisions with
artifact ids as the data-plane references.

## Stable public capability interface

External provider growth does not expand the ChatGPT MCP schema one tool at a
time. ChatGPT uses:

1. `capability_search`
2. `capability_describe`
3. `capability_invoke`

Provider-specific schemas remain behind the Broker. Only reviewed/allowlisted
capabilities enter the Registry.

## Artifact Plane

Binary document bytes remain outside model textual context.

```text
local file
  → immutable artifact
  → provider sandbox input
  → external MCP
  → managed output
  → immutable output artifact
  → next provider
```

Artifacts carry SHA-256, size, MIME, TTL and provenance. Provider contracts may
tighten input/output size, count, TTL and MIME policy, subject to PLA hard caps.

## Provider plane

Provider installation and runtime declaration are separate:

- `provider_specs/<id>.txt`: exact dependency pins.
- `provider_manifests/<id>.json`: process/runtime and capability policy.
- `.provider_envs/<id>/`: ignored isolated environment.

v1.0 ships reviewed manifests for MarkItDown, DOCX and PDF providers. Provider
Doctor checks lifecycle health and pinned-version drift.

## Durable local runtime

The local executor retains the v0.8 foundations:

- named workspace roots and protected paths
- bounded filesystem/search/process/PowerShell tools
- SQLite persistent task store
- cursor observations
- controlled cancellation
- SHA-256 optimistic write concurrency
- single-file patching
- multi-file ChangeSet with preflight/stage/commit/rollback

These are execution capabilities, not an embedded agent loop.

## v1 acceptance evidence

The pre-freeze chain has demonstrated all of the following with real providers:

- local artifact → Microsoft MarkItDown MCP
- Markdown artifact → DOCX MCP → DOCX artifact
- DOCX artifact → MarkItDown MCP
- 15.6 MB real PDF → PDF MCP mutation → new PDF artifact
- mutated PDF → MarkItDown MCP
- page-count preservation, changed SHA, valid MIME/signature and valid
  provenance
- provider dependency isolation and version-drift checks
- three real providers concurrently healthy while raw provider tools remain
  absent from the public MCP schema

The regression suite must remain green at freeze time.

## Non-goals retained for v1

- no server-side LLM Agent Brain
- no API-key model fallback
- no arbitrary shell
- no arbitrary provider network downloader
- no binary Base64 through model context
- no automatic business-call retry
- no automatic provider-to-provider planning
- no claim of OS-level sandboxing
