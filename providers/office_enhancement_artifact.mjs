import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [packageRoot, specPath, outputPath, previewPath] = process.argv.slice(2);
if (!packageRoot || !specPath || !outputPath) {
  throw new Error("usage: office_enhancement_artifact.mjs <packageRoot> <specPath> <outputPptx> [previewPng|-]");
}

const packageJson = JSON.parse(
  await fs.readFile(path.join(packageRoot, "package.json"), "utf8"),
);
const exportEntry = packageJson?.exports?.["."];
if (typeof exportEntry !== "string") {
  throw new Error("@oai/artifact-tool package root export is missing");
}
const entryPath = path.resolve(packageRoot, exportEntry);
const { Presentation, PresentationFile } = await import(pathToFileURL(entryPath).href);
const spec = JSON.parse(await fs.readFile(specPath, "utf8"));

const presentation = Presentation.create({
  slideSize: {
    width: Number(spec.slide_size?.width ?? 1280),
    height: Number(spec.slide_size?.height ?? 720),
  },
});
const slide = presentation.slides.add();
if (spec.background) {
  slide.background.fill = spec.background;
}

const byId = new Map();

function positionOf(element) {
  const p = element.position ?? {};
  return {
    left: Number(p.left ?? 0),
    top: Number(p.top ?? 0),
    width: Number(p.width ?? 100),
    height: Number(p.height ?? 60),
    ...(p.rotation != null ? { rotation: Number(p.rotation) } : {}),
  };
}

function textStyleOf(element) {
  const style = element.text_style ?? {};
  const out = {};
  for (const key of [
    "fontSize",
    "bold",
    "italic",
    "color",
    "alignment",
    "verticalAlignment",
    "autoFit",
    "wrap",
    "typeface",
    "lineSpacing",
    "insets",
  ]) {
    if (style[key] != null) out[key] = style[key];
  }
  return out;
}

for (const element of spec.elements ?? []) {
  if (element.kind === "connector") continue;

  if (element.kind === "image") {
    const bytes = await fs.readFile(element.path);
    const exact = bytes.buffer.slice(
      bytes.byteOffset,
      bytes.byteOffset + bytes.byteLength,
    );
    const ext = path.extname(element.path).toLowerCase();
    const contentType =
      element.content_type ??
      ({
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
      }[ext] ?? "application/octet-stream");
    const image = slide.images.add({
      blob: exact,
      contentType,
      alt: element.alt ?? element.id ?? "image",
      fit: element.fit ?? "contain",
      position: positionOf(element),
      ...(element.geometry ? { geometry: element.geometry } : {}),
      ...(element.border_radius != null
        ? { borderRadius: element.border_radius }
        : {}),
      ...(element.crop ? { crop: element.crop } : {}),
    });
    byId.set(element.id, image);
    continue;
  }

  const shape = slide.shapes.add({
    geometry: element.kind === "text" ? "textbox" : (element.geometry ?? "rect"),
    name: element.name ?? element.id,
    position: positionOf(element),
    ...(element.fill != null ? { fill: element.fill } : {}),
    ...(element.line != null ? { line: element.line } : {}),
    ...(element.border_radius != null
      ? { borderRadius: element.border_radius }
      : {}),
    ...(element.shadow != null ? { shadow: element.shadow } : {}),
  });
  if (element.text != null) {
    shape.text = String(element.text);
    const style = textStyleOf(element);
    if (Object.keys(style).length) shape.text.style = style;
  }
  byId.set(element.id, shape);
}

for (const element of spec.elements ?? []) {
  if (element.kind !== "connector") continue;
  const from = byId.get(element.from);
  const to = byId.get(element.to);
  if (!from || !to) {
    throw new Error(`connector endpoint missing: ${element.from} -> ${element.to}`);
  }
  slide.shapes.connect(from, to, {
    kind: element.connector_kind ?? "elbow",
    ...(element.from_side ? { fromSide: element.from_side } : {}),
    ...(element.to_side ? { toSide: element.to_side } : {}),
    line:
      element.line ??
      { style: "solid", fill: "#64748B", width: 2 },
    ...(element.head ? { head: element.head } : {}),
    ...(element.tail ? { tail: element.tail } : {}),
  });
}

const pptx = await PresentationFile.exportPptx(presentation);
await pptx.save(outputPath);

if (previewPath && previewPath !== "-") {
  const png = await presentation.export({
    slide,
    format: "png",
    scale: Number(spec.preview_scale ?? 1),
  });
  await fs.writeFile(previewPath, new Uint8Array(await png.arrayBuffer()));
}

console.log(
  JSON.stringify({
    status: "completed",
    elements: spec.elements?.length ?? 0,
    output: outputPath,
    preview: previewPath === "-" ? null : previewPath,
    artifactToolVersion: packageJson.version ?? null,
  }),
);
