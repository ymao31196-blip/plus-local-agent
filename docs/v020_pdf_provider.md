# v0.20 Real PDF Provider E2E

PLA v0.20 adds the first reviewed PDF-manipulation provider to the declarative
Capability Runtime and completes the final real-provider exercise before v1.0
freeze work.

## Provider

Provider id: `pdf`

Package:

- `pdf-mcp-server==0.1.2`
- isolated environment: `.provider_envs/pdf/`
- transport: stdio
- MCP mode: auto
- autostart: true

The upstream server exposes several PDF tools, but PLA v0.20 deliberately
allowlists only:

`pdf.add_text_watermark_direct`

The other remote PDF tools are not registered in the Capability Registry.

Import-time third-party warnings are redirected to stderr during provider
bootstrap so stdout remains reserved for MCP JSON-RPC.

## Capability contract

Input:

- one PLA PDF artifact
- watermark text
- reviewed visual parameters

PLA-managed output:

- `watermarked.pdf`
- MIME: `application/pdf`

Policy:

- input <= 64 MiB
- output <= 64 MiB
- total output <= 64 MiB
- one output artifact
- output TTL 600 seconds
- PDF input only
- PDF output only
- `write_local` risk
- exact `confirmation="INVOKE"` required

The caller cannot supply `output_path`; PLA allocates it inside the per-invocation
sandbox and imports the result into the immutable Artifact Store.

## Real E2E

Input document:

`rerun_thesis/energies-19-00103-v2.pdf`

Observed source:

- size: 15,577,425 bytes
- pages: 28
- SHA-256:
  `edc54b4cdd5b155d8c0d3e02c237360eeaccb4b4c81871f22dff0ebd3277dce9`

Operation:

- text: `PLA v0.20 E2E`
- layout: single
- position: bottom-right
- font size: 12
- opacity: 0.03

Observed output:

- size: 15,593,242 bytes
- pages: 28
- SHA-256:
  `6fd8ab6a9bdcf1d7fe34c6035b372334e299a98b9dfdac43229c5c34eb3d4a5a`
- source/output SHA differ: PASS
- page count preserved: PASS
- PDF content signature policy: PASS
- direct parent artifact recorded: PASS
- recursive provenance verification: PASS
- temporary capability sandbox path leakage: none

The generated PDF artifact was then passed to the existing Microsoft MarkItDown
provider:

`PDF artifact -> pdf.add_text_watermark_direct -> PDF artifact
              -> markitdown.convert_to_markdown -> Markdown text`

MarkItDown completed successfully. The article heading and title remained
extractable, and the selectable watermark text was present in extracted text.

## Provider health

Provider Doctor verifies exact versions for:

- `pdf-mcp-server==0.1.2`
- `fastmcp==4.0.3`
- `mcp==2.2.0`
- `pikepdf==10.13.0.post1`
- `PyMuPDF==1.28.2`

At v0.20 acceptance, the PDF provider reported ready/healthy with zero version
drift and one registered capability.

## Distribution note

Local package metadata reports:

- `pdf-mcp-server 0.1.2`: MIT
- `pikepdf 10.13.0.post1`: MPL-2.0
- `PyMuPDF 1.28.2`: dual licensed under GNU AGPL 3.0 or an Artifex commercial
  license

This does not block the current local-use architecture validation. Any future
distribution of a bundled PLA + PDF provider environment should include a
separate dependency/license review.

## v1.0 interpretation

v0.20 proves that the same stable ChatGPT-facing broker can host:

- a read/document-conversion provider (MarkItDown)
- a document-generation provider (DOCX)
- a real PDF mutation provider

without exposing each provider's raw tool catalog to ChatGPT and without passing
binary document bytes through model context.

Further PDF tools should be enabled one at a time through the existing manifest,
policy, confirmation and Artifact Plane controls rather than by exposing the
upstream PDF server wholesale.
