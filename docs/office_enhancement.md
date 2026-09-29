# Office补强

Office补强 is PLA's specialist Office/PDF engineering Provider. It complements the WPS Office Provider rather than replacing it.

Use WPS for routine document, spreadsheet and presentation editing. Use `office-enhancement` when the task needs low-level PPTX package work, editable technical overlays, Microsoft PowerPoint rendering QA, or PDF figure extraction.

## Runtime model

~~~text
ChatGPT
  |
  +-- WPS Provider
  |     ordinary Office editing
  |
  +-- office-enhancement
        |
        +-- @oai/artifact-tool structured adapter
        +-- zipfile + lxml OOXML merge
        +-- Microsoft PowerPoint COM inspection/rendering
        +-- PyMuPDF extraction/rendering
~~~

The Provider does not expose arbitrary JavaScript execution.

The artifact-tool path accepts a structured specification containing editable shapes, text, images and connectors. The fixed Node adapter resolves the installed Codex primary-runtime `@oai/artifact-tool` package through its declared `package.json` export entry.

If artifact-tool is unavailable, the remaining OOXML, COM and PyMuPDF capabilities can still operate.

## Capabilities

### `office_enhancement_status`

Reports:

- isolated Python dependency versions;
- Node.js availability;
- Codex runtime artifact-tool location/version;
- Microsoft PowerPoint COM registration;
- WPS Presentation COM registration.

### `artifact_build_overlay`

Creates one editable PPTX overlay slide from a structured element specification.

Supported element kinds:

- `shape`
- `text`
- `image`
- `connector`

Image paths are resolved through PLA's normal authorized-root policy. Connector endpoints must reference elements that have already been declared.

### `pptx_inspect`

Reads PPTX ZIP/OpenXML structure without launching Office.

Reports:

- slide count and slide size;
- master/layout/media counts;
- per-slide shape/picture/connector counts;
- bounded text previews.

### `pptx_merge_overlay`

Merges editable overlay objects into one target slide while preserving the original PPTX package.

The merge path:

1. copies only supported slide objects;
2. discovers relationship IDs actually referenced by those objects;
3. ignores unrelated overlay relationships such as notes slides;
4. remaps relationship IDs;
5. copies referenced media under collision-free package names;
6. remaps shape IDs and connector endpoint IDs;
7. leaves unrelated slides, masters and layouts unchanged.

The current merge surface is intentionally narrower than arbitrary OOXML import. Unsupported internal relationships are rejected rather than copied blindly.

### `powerpoint_inspect`

Opens the PPTX through the locally installed Microsoft PowerPoint COM engine.

It records actual PowerPoint shape geometry and text bounds and flags likely horizontal/vertical text overflow.

### `powerpoint_render_slide`

Uses Microsoft PowerPoint to export one slide to PNG for visual QA.

### `powerpoint_render_pdf`

Uses Microsoft PowerPoint to render the full deck to PDF.

### `pdf_extract_images`

Uses PyMuPDF to extract embedded raster images from selected PDF pages.

### `pdf_render_region`

Uses PyMuPDF to render a full page or clip rectangle to PNG. This is the preferred fallback for charts/figures represented as PDF vector graphics rather than embedded raster images.

## Dependency boundary

Python dependencies are isolated in:

~~~text
.provider_envs/office-enhancement/
~~~

Reviewed pinned dependencies:

~~~text
fastmcp==4.0.3
mcp==2.2.0
lxml==6.1.3
PyMuPDF==1.28.2
pywin32==312
~~~

The Codex runtime artifact-tool package is not copied or vendored into PLA.

At invocation time the Provider first checks:

~~~text
%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\@oai\artifact-tool
~~~

and then falls back to other installed Codex runtime directories. An explicit local override can be supplied through `OFFICE_ENHANCEMENT_ARTIFACT_TOOL_ROOT`.

## Safety boundary

- no arbitrary JavaScript execution;
- no generic Node.js tool is exposed;
- all file paths go through PLA authorized roots;
- source PPTX files are not overwritten by `pptx_merge_overlay`;
- unsupported OOXML relationship types are rejected;
- PowerPoint COM work is bounded to open/inspect/export operations;
- ordinary WPS editing remains separate from this specialist Provider.

## Verified E2E

The initial Office补强 E2E created a PowerPoint-authored base deck containing an existing header, generated an editable artifact-tool overlay with shapes/text/connector/SVG media, merged the overlay through OOXML, reopened the merged file through Microsoft PowerPoint, inspected text geometry, exported PNG/PDF, extracted an embedded image through PyMuPDF and rendered a PDF clip to PNG.

The original header survived the merge and PowerPoint reported no likely text overflow in the inserted test objects.
