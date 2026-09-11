# Artifact Security and Governance (v0.19)

PLA v0.19 hardens the Artifact Plane used for cross-provider file transfer.

## Capability artifact policy

A capability may declare `artifact_contract.policy`. PLA normalizes the policy
and enforces global hard caps, so a manifest can tighten limits but cannot create
an unbounded artifact capability.

Supported policy fields:

- `max_input_bytes`
- `max_output_bytes`
- `max_total_output_bytes`
- `max_output_artifacts`
- `output_ttl_seconds`
- `allowed_input_mime_types`
- `allowed_output_mime_types`

Default limits are conservative and bounded. Provider-specific manifests should
declare narrower limits where the capability contract is known.

Policy enforcement happens before staging provider inputs and before importing
provider outputs back into the immutable Artifact Store.

## MIME and lightweight content validation

MIME values are normalized and validated syntactically.

For formats PLA explicitly understands, the runtime also performs lightweight
content checks before crossing the provider boundary:

- `text/*`: UTF-8 decode must succeed.
- `application/pdf`: payload must start with `%PDF-`.
- DOCX: ZIP container must contain `[Content_Types].xml` and
  `word/document.xml`.
- PPTX: ZIP container must contain `[Content_Types].xml` and
  `ppt/presentation.xml`.
- XLSX: ZIP container must contain `[Content_Types].xml` and
  `xl/workbook.xml`.

This is format validation, not malware scanning or antivirus inspection.

## Provenance integrity

Provider-generated artifacts record both the direct parent artifact ids and a
snapshot of each parent's:

- artifact id
- SHA-256
- size
- name
- MIME type

`artifact_verify` checks the current artifact payload and can recursively verify
recorded parent snapshots. This detects missing parents, payload corruption,
metadata size/SHA corruption, provenance mismatch and provenance cycles.

The provenance mechanism is an internal integrity/audit mechanism. It is not a
cryptographic signature against a malicious actor who can arbitrarily rewrite
both payloads and all PLA metadata on disk.

## Artifact GC

`artifact_gc` is reference-aware.

The GC root set is every non-expired artifact. PLA recursively preserves all
ancestors referenced by those live artifacts, even when an ancestor's own TTL has
expired. Only expired artifacts that are not reachable from a live artifact are
eligible for collection.

The MCP tool defaults to `dry_run=true`. Actual deletion requires both:

- `dry_run=false`
- `confirmation="GC"`

Invalid/corrupt store entries are reported but are not automatically deleted.

## Provider output governance

Provider outputs must remain inside the per-invocation `.capability_io` sandbox.
The Artifact Runtime enforces per-file size, aggregate output size, output count,
MIME policy and TTL before creating immutable output artifacts.

Managed output paths are allocated by PLA. Providers do not choose arbitrary local
destinations.

## Current production policies

`docx.create_from_markdown`:

- Markdown/plain-text input only
- 8 MiB input limit
- one DOCX output
- 32 MiB output/aggregate limit
- 600 second output TTL
- DOCX MIME only

`markitdown.convert_to_markdown`:

- 64 MiB input limit
- broad input MIME compatibility retained
- no artifact output

These limits can be tightened through provider manifests without changing PLA
core code.
