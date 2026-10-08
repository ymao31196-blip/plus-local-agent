from __future__ import annotations

import copy
import importlib.metadata
import json
import mimetypes
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import winreg
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from fastmcp import FastMCP
from lxml import etree

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tooling.local_tools import safe_path  # noqa: E402


mcp = FastMCP("Office Enhancement", version="0.1.0")

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"p": P_NS, "a": A_NS, "r": R_NS}
REL_TAG = f"{{{PKG_REL_NS}}}Relationship"
RID_RE = re.compile(r"^rId(\d+)$")
IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".svg": "image/svg+xml",
}


def _resolve(root: str, path: str, mode: str) -> Path:
    target = safe_path(path, root, mode)
    if mode == "write":
        target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _artifact_tool_root() -> Path | None:
    configured = os.environ.get("OFFICE_ENHANCEMENT_ARTIFACT_TOOL_ROOT", "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        return path if (path / "package.json").is_file() else None

    primary = (
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "node"
        / "node_modules"
        / "@oai"
        / "artifact-tool"
    )
    if (primary / "package.json").is_file():
        return primary.resolve()

    runtime_root = Path.home() / ".cache" / "codex-runtimes"
    if runtime_root.is_dir():
        for package_json in runtime_root.glob(
            "*/dependencies/node/node_modules/@oai/artifact-tool/package.json"
        ):
            return package_json.parent.resolve()
    return None


def _artifact_tool_info() -> dict[str, Any]:
    root = _artifact_tool_root()
    if root is None:
        return {"available": False, "root": None, "version": None, "entry": None}
    payload = json.loads((root / "package.json").read_text(encoding="utf-8"))
    exports = payload.get("exports") or {}
    entry_rel = exports.get(".") if isinstance(exports, dict) else None
    entry = (root / entry_rel).resolve() if isinstance(entry_rel, str) else None
    return {
        "available": bool(entry and entry.is_file()),
        "root": str(root),
        "version": payload.get("version"),
        "entry": str(entry) if entry else None,
    }


def _com_registered(progid: str) -> bool:
    try:
        winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, f"{progid}\\CLSID")
        return True
    except OSError:
        return False


def _require_pptx(path: Path) -> None:
    if path.suffix.casefold() != ".pptx":
        raise ValueError("path must end with .pptx")
    if not path.is_file():
        raise ValueError(f"PPTX file does not exist: {path}")


def _require_pdf(path: Path) -> None:
    if path.suffix.casefold() != ".pdf":
        raise ValueError("path must end with .pdf")
    if not path.is_file():
        raise ValueError(f"PDF file does not exist: {path}")


def _node_executable() -> str:
    node = shutil.which("node.exe") or shutil.which("node")
    if not node:
        raise RuntimeError("Node.js was not found")
    return str(Path(node).resolve())


def _normalize_overlay_spec(root: str, spec: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValueError("spec must be an object")
    slide_size = spec.get("slide_size") or {"width": 1280, "height": 720}
    width = float(slide_size.get("width", 1280))
    height = float(slide_size.get("height", 720))
    if not (100 <= width <= 10000 and 100 <= height <= 10000):
        raise ValueError("slide_size is out of bounds")

    elements = spec.get("elements")
    if not isinstance(elements, list) or not elements or len(elements) > 300:
        raise ValueError("spec.elements must contain 1-300 elements")

    normalized = dict(spec)
    normalized["slide_size"] = {"width": width, "height": height}
    out_elements: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for index, raw in enumerate(elements):
        if not isinstance(raw, dict):
            raise ValueError(f"element {index} must be an object")
        item = copy.deepcopy(raw)
        kind = str(item.get("kind", "")).strip()
        if kind not in {"shape", "text", "image", "connector"}:
            raise ValueError(f"unsupported element kind: {kind}")
        element_id = str(item.get("id", "")).strip()
        if kind != "connector":
            if not element_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", element_id):
                raise ValueError(f"element {index} requires a simple id")
            if element_id in seen_ids:
                raise ValueError(f"duplicate element id: {element_id}")
            seen_ids.add(element_id)
        if kind == "image":
            image_path = item.get("path")
            if not isinstance(image_path, str) or not image_path:
                raise ValueError("image element requires path")
            resolved = _resolve(root, image_path, "read")
            if not resolved.is_file():
                raise ValueError(f"image file does not exist: {image_path}")
            item["path"] = str(resolved)
        if kind == "connector":
            if item.get("from") not in seen_ids or item.get("to") not in seen_ids:
                raise ValueError(
                    "connector endpoints must reference earlier shape/text/image ids"
                )
        out_elements.append(item)
    normalized["elements"] = out_elements
    return normalized


@mcp.tool
def office_enhancement_status() -> dict[str, Any]:
    """Report Office enhancement dependencies, COM registrations and artifact-tool availability."""
    return {
        "status": "ready",
        "provider_version": "0.1.0",
        "python": sys.version.split()[0],
        "lxml": _package_version("lxml"),
        "pymupdf": _package_version("PyMuPDF"),
        "pywin32": _package_version("pywin32"),
        "node": shutil.which("node.exe") or shutil.which("node"),
        "artifact_tool": _artifact_tool_info(),
        "com": {
            "powerpoint": _com_registered("PowerPoint.Application"),
            "wps_presentation": _com_registered("KWPP.Application"),
            "wps_ket": _com_registered("Ket.Application"),
        },
    }


@mcp.tool
def artifact_build_overlay(
    root: str,
    output_path: str,
    spec: dict[str, Any],
    preview_path: str | None = None,
) -> dict[str, Any]:
    """Build one editable PPTX overlay slide from structured shapes/text/images/connectors."""
    info = _artifact_tool_info()
    if not info["available"]:
        raise RuntimeError("@oai/artifact-tool is not available in the Codex runtime")
    output = _resolve(root, output_path, "write")
    if output.suffix.casefold() != ".pptx":
        raise ValueError("output_path must end with .pptx")
    preview = None
    if preview_path:
        preview = _resolve(root, preview_path, "write")
        if preview.suffix.casefold() != ".png":
            raise ValueError("preview_path must end with .png")

    normalized = _normalize_overlay_spec(root, spec)
    helper = (PROJECT_ROOT / "providers" / "office_enhancement_artifact.mjs").resolve()
    if not helper.is_file():
        raise RuntimeError("artifact-tool helper is missing")

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix="pla-office-overlay-",
        delete=False,
        encoding="utf-8",
    ) as handle:
        json.dump(normalized, handle, ensure_ascii=False)
        spec_path = Path(handle.name)

    try:
        argv = [
            _node_executable(),
            str(helper),
            str(info["root"]),
            str(spec_path),
            str(output),
            str(preview) if preview else "-",
        ]
        completed = subprocess.run(
            argv,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            shell=False,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                completed.stderr[-6000:].strip()
                or completed.stdout[-6000:].strip()
                or f"artifact-tool exited with {completed.returncode}"
            )
        if not output.is_file():
            raise RuntimeError("artifact-tool did not create the requested PPTX")
        return {
            "status": "completed",
            "output_path": output_path,
            "output_bytes": output.stat().st_size,
            "preview_path": preview_path if preview and preview.is_file() else None,
            "artifact_tool_version": info["version"],
            "element_count": len(normalized["elements"]),
            "stdout": completed.stdout[-4000:],
        }
    finally:
        spec_path.unlink(missing_ok=True)


def _slide_path(index: int) -> str:
    if not isinstance(index, int) or index < 1:
        raise ValueError("slide index must be >= 1")
    return f"ppt/slides/slide{index}.xml"


def _rels_path(slide_path: str) -> str:
    p = PurePosixPath(slide_path)
    return str(p.parent / "_rels" / f"{p.name}.rels")


def _relationship_root(data: bytes | None) -> etree._Element:
    if data:
        return etree.fromstring(data)
    return etree.Element(f"{{{PKG_REL_NS}}}Relationships", nsmap={None: PKG_REL_NS})


def _next_rid(rel_root: etree._Element) -> str:
    used = []
    for rel in rel_root.findall(REL_TAG):
        match = RID_RE.match(rel.get("Id", ""))
        if match:
            used.append(int(match.group(1)))
    return f"rId{max(used, default=0) + 1}"


def _content_type_default(content_types: etree._Element, extension: str) -> None:
    extension = extension.lstrip(".").casefold()
    existing = {
        node.get("Extension", "").casefold()
        for node in content_types.findall(f"{{{CT_NS}}}Default")
    }
    if extension in existing:
        return
    content_type = IMAGE_CONTENT_TYPES.get(f".{extension}") or mimetypes.types_map.get(
        f".{extension}"
    )
    if not content_type:
        raise ValueError(f"unknown media content type: .{extension}")
    node = etree.SubElement(content_types, f"{{{CT_NS}}}Default")
    node.set("Extension", extension)
    node.set("ContentType", content_type)


@mcp.tool
def pptx_inspect(root: str, input_path: str) -> dict[str, Any]:
    """Inspect PPTX package structure without changing the deck."""
    source = _resolve(root, input_path, "read")
    _require_pptx(source)
    with zipfile.ZipFile(source, "r") as zf:
        names = set(zf.namelist())
        slides = sorted(
            [
                name
                for name in names
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            ],
            key=lambda name: int(re.search(r"(\d+)", name).group(1)),
        )
        summaries = []
        for name in slides:
            xml = etree.fromstring(zf.read(name))
            texts = [
                text
                for text in xml.xpath(".//a:t/text()", namespaces=NS)
                if isinstance(text, str) and text.strip()
            ]
            summaries.append(
                {
                    "slide": int(re.search(r"(\d+)", name).group(1)),
                    "shape_count": len(xml.xpath(".//p:sp", namespaces=NS)),
                    "picture_count": len(xml.xpath(".//p:pic", namespaces=NS)),
                    "connector_count": len(xml.xpath(".//p:cxnSp", namespaces=NS)),
                    "graphic_frame_count": len(
                        xml.xpath(".//p:graphicFrame", namespaces=NS)
                    ),
                    "text_preview": " | ".join(texts)[:500],
                }
            )

        width = height = None
        if "ppt/presentation.xml" in names:
            presentation = etree.fromstring(zf.read("ppt/presentation.xml"))
            size = presentation.find(f".//{{{P_NS}}}sldSz")
            if size is not None:
                width = int(size.get("cx")) if size.get("cx") else None
                height = int(size.get("cy")) if size.get("cy") else None

        return {
            "status": "completed",
            "input_path": input_path,
            "slide_count": len(slides),
            "slide_size_emu": {"width": width, "height": height},
            "master_count": sum(
                1 for n in names if re.fullmatch(r"ppt/slideMasters/slideMaster\d+\.xml", n)
            ),
            "layout_count": sum(
                1 for n in names if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n)
            ),
            "media_count": sum(1 for n in names if n.startswith("ppt/media/")),
            "slides": summaries,
        }


@mcp.tool
def pptx_merge_overlay(
    root: str,
    input_path: str,
    overlay_path: str,
    target_slide: int,
    output_path: str,
    overlay_slide: int = 1,
) -> dict[str, Any]:
    """Merge editable overlay shapes and images into one existing PPTX slide via OOXML."""
    source = _resolve(root, input_path, "read")
    overlay = _resolve(root, overlay_path, "read")
    output = _resolve(root, output_path, "write")
    _require_pptx(source)
    _require_pptx(overlay)
    if output.suffix.casefold() != ".pptx":
        raise ValueError("output_path must end with .pptx")
    if output.resolve() == source.resolve():
        raise ValueError("output_path must differ from input_path")

    target_slide_path = _slide_path(target_slide)
    donor_slide_path = _slide_path(overlay_slide)
    target_rels_path = _rels_path(target_slide_path)
    donor_rels_path = _rels_path(donor_slide_path)

    with zipfile.ZipFile(source, "r") as base_zip, zipfile.ZipFile(
        overlay, "r"
    ) as donor_zip:
        package = {name: base_zip.read(name) for name in base_zip.namelist()}
        donor_names = set(donor_zip.namelist())
        if target_slide_path not in package:
            raise ValueError(f"target slide does not exist: {target_slide}")
        if donor_slide_path not in donor_names:
            raise ValueError(f"overlay slide does not exist: {overlay_slide}")

        target_xml = etree.fromstring(package[target_slide_path])
        donor_xml = etree.fromstring(donor_zip.read(donor_slide_path))
        target_tree = target_xml.find(f".//{{{P_NS}}}spTree")
        donor_tree = donor_xml.find(f".//{{{P_NS}}}spTree")
        if target_tree is None or donor_tree is None:
            raise ValueError("slide shape tree is missing")

        target_rels = _relationship_root(package.get(target_rels_path))
        donor_rels = _relationship_root(
            donor_zip.read(donor_rels_path) if donor_rels_path in donor_names else None
        )
        rid_map: dict[str, str] = {}
        copied_media = 0
        media_counter = 1

        content_types = etree.fromstring(package["[Content_Types].xml"])
        existing_media = {n for n in package if n.startswith("ppt/media/")}

        allowed_tags = {
            f"{{{P_NS}}}sp",
            f"{{{P_NS}}}pic",
            f"{{{P_NS}}}cxnSp",
            f"{{{P_NS}}}graphicFrame",
        }
        donor_children = [child for child in donor_tree if child.tag in allowed_tags]
        if not donor_children:
            raise ValueError("overlay slide contains no mergeable elements")
        referenced_rids = {
            value
            for child in donor_children
            for element in child.iter()
            for key, value in element.attrib.items()
            if key.startswith(f"{{{R_NS}}}") and value
        }

        for rel in donor_rels.findall(REL_TAG):
            old_rid = rel.get("Id")
            rel_type = rel.get("Type", "")
            target = rel.get("Target", "")
            target_mode = rel.get("TargetMode")
            if not old_rid or old_rid not in referenced_rids:
                continue

            new_rid = _next_rid(target_rels)
            new_rel = etree.SubElement(target_rels, REL_TAG)
            new_rel.set("Id", new_rid)
            new_rel.set("Type", rel_type)
            if target_mode:
                new_rel.set("TargetMode", target_mode)

            if target_mode == "External":
                new_rel.set("Target", target)
            elif target.startswith("../media/") or target.startswith("/ppt/media/"):
                if target.startswith("/ppt/media/"):
                    donor_media_path = target.lstrip("/")
                else:
                    donor_media_path = posixpath.normpath(
                        posixpath.join(posixpath.dirname(donor_slide_path), target)
                    )
                if donor_media_path not in donor_names:
                    raise ValueError(f"overlay media is missing: {donor_media_path}")
                ext = PurePosixPath(donor_media_path).suffix.casefold()
                while True:
                    candidate = f"ppt/media/officeEnhancement_{media_counter}{ext}"
                    media_counter += 1
                    if candidate not in existing_media:
                        break
                package[candidate] = donor_zip.read(donor_media_path)
                existing_media.add(candidate)
                _content_type_default(content_types, ext)
                new_rel.set("Target", f"../media/{PurePosixPath(candidate).name}")
                copied_media += 1
            else:
                raise ValueError(
                    "overlay contains unsupported internal relationship: "
                    f"{rel_type} -> {target}"
                )
            rid_map[old_rid] = new_rid

        current_ids = [
            int(value)
            for value in target_xml.xpath(".//p:cNvPr/@id", namespaces=NS)
            if str(value).isdigit()
        ]
        next_shape_id = max(current_ids, default=0) + 1
        shape_id_map: dict[str, str] = {}

        copies = [copy.deepcopy(child) for child in donor_children]
        for node in copies:
            for c_nv_pr in node.xpath(".//p:cNvPr", namespaces=NS):
                old_id = c_nv_pr.get("id")
                if old_id:
                    shape_id_map[old_id] = str(next_shape_id)
                    c_nv_pr.set("id", str(next_shape_id))
                    next_shape_id += 1

        for node in copies:
            for element in node.iter():
                for key, value in list(element.attrib.items()):
                    if key.startswith(f"{{{R_NS}}}") and value in rid_map:
                        element.set(key, rid_map[value])
                if element.tag in {
                    f"{{{A_NS}}}stCxn",
                    f"{{{A_NS}}}endCxn",
                }:
                    old_id = element.get("id")
                    if old_id in shape_id_map:
                        element.set("id", shape_id_map[old_id])
            target_tree.append(node)

        package[target_slide_path] = etree.tostring(
            target_xml, xml_declaration=True, encoding="UTF-8", standalone=True
        )
        package[target_rels_path] = etree.tostring(
            target_rels, xml_declaration=True, encoding="UTF-8", standalone=True
        )
        package["[Content_Types].xml"] = etree.tostring(
            content_types, xml_declaration=True, encoding="UTF-8", standalone=True
        )

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as out_zip:
        for name, data in package.items():
            out_zip.writestr(name, data)

    return {
        "status": "completed",
        "input_path": input_path,
        "overlay_path": overlay_path,
        "target_slide": target_slide,
        "overlay_slide": overlay_slide,
        "output_path": output_path,
        "elements_added": len(copies),
        "media_copied": copied_media,
        "output_bytes": output.stat().st_size,
    }


def _open_powerpoint(path: Path):
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise RuntimeError("pywin32 is not installed in the Provider environment") from exc

    pythoncom.CoInitialize()
    app = win32com.client.DispatchEx("PowerPoint.Application")
    presentation = app.Presentations.Open(str(path), True, False, False)
    return pythoncom, app, presentation


def _close_powerpoint(pythoncom, app, presentation) -> None:
    try:
        presentation.Close()
    finally:
        try:
            try:
                app.Quit()
            except (AttributeError, TypeError):
                try:
                    app.Application.Quit()
                except (AttributeError, TypeError):
                    presentation.Application.Quit()
        finally:
            pythoncom.CoUninitialize()


@mcp.tool
def powerpoint_inspect(
    root: str,
    input_path: str,
    slide_numbers: list[int] | None = None,
) -> dict[str, Any]:
    """Inspect rendered PowerPoint shape/text geometry and flag likely text overflow."""
    source = _resolve(root, input_path, "read")
    _require_pptx(source)
    pythoncom, app, presentation = _open_powerpoint(source)
    try:
        selected = (
            set(slide_numbers)
            if slide_numbers
            else set(range(1, presentation.Slides.Count + 1))
        )
        slides = []
        for slide in presentation.Slides:
            if slide.SlideIndex not in selected:
                continue
            shapes = []
            for shape in slide.Shapes:
                item = {
                    "id": int(shape.Id),
                    "name": str(shape.Name),
                    "type": int(shape.Type),
                    "left": round(float(shape.Left), 2),
                    "top": round(float(shape.Top), 2),
                    "width": round(float(shape.Width), 2),
                    "height": round(float(shape.Height), 2),
                }
                try:
                    if bool(shape.HasTextFrame) and bool(shape.TextFrame2.HasText):
                        frame = shape.TextFrame2
                        text_range = frame.TextRange
                        inner_width = max(
                            0.0,
                            float(shape.Width)
                            - float(frame.MarginLeft)
                            - float(frame.MarginRight),
                        )
                        inner_height = max(
                            0.0,
                            float(shape.Height)
                            - float(frame.MarginTop)
                            - float(frame.MarginBottom),
                        )
                        bound_width = float(text_range.BoundWidth)
                        bound_height = float(text_range.BoundHeight)
                        item["text"] = str(text_range.Text)[:2000]
                        item["text_metrics"] = {
                            "bound_width": round(bound_width, 2),
                            "bound_height": round(bound_height, 2),
                            "inner_width": round(inner_width, 2),
                            "inner_height": round(inner_height, 2),
                            "likely_overflow_x": bound_width > inner_width + 1.0,
                            "likely_overflow_y": bound_height > inner_height + 1.0,
                            "auto_size": int(frame.AutoSize),
                            "word_wrap": int(frame.WordWrap),
                        }
                except Exception as exc:
                    item["text_inspection_error"] = type(exc).__name__
                shapes.append(item)
            slides.append(
                {
                    "slide": int(slide.SlideIndex),
                    "shape_count": int(slide.Shapes.Count),
                    "shapes": shapes,
                }
            )
        return {
            "status": "completed",
            "input_path": input_path,
            "slide_count": int(presentation.Slides.Count),
            "slides": slides,
        }
    finally:
        _close_powerpoint(pythoncom, app, presentation)


@mcp.tool
def powerpoint_render_slide(
    root: str,
    input_path: str,
    slide_number: int,
    output_path: str,
    width: int = 1600,
    height: int = 900,
) -> dict[str, Any]:
    """Render one slide with the locally installed Microsoft PowerPoint engine."""
    source = _resolve(root, input_path, "read")
    output = _resolve(root, output_path, "write")
    _require_pptx(source)
    if output.suffix.casefold() != ".png":
        raise ValueError("output_path must end with .png")
    if not (1 <= slide_number):
        raise ValueError("slide_number must be >= 1")
    if not (320 <= width <= 7680 and 240 <= height <= 4320):
        raise ValueError("render dimensions are out of bounds")

    pythoncom, app, presentation = _open_powerpoint(source)
    try:
        if slide_number > presentation.Slides.Count:
            raise ValueError("slide_number exceeds slide count")
        presentation.Slides(slide_number).Export(
            str(output), "PNG", int(width), int(height)
        )
    finally:
        _close_powerpoint(pythoncom, app, presentation)
    if not output.is_file():
        raise RuntimeError("PowerPoint did not create the PNG")
    return {
        "status": "completed",
        "input_path": input_path,
        "slide_number": slide_number,
        "output_path": output_path,
        "output_bytes": output.stat().st_size,
        "width": width,
        "height": height,
    }


@mcp.tool
def powerpoint_render_pdf(
    root: str,
    input_path: str,
    output_path: str,
) -> dict[str, Any]:
    """Render a PPTX to PDF using the locally installed Microsoft PowerPoint engine."""
    source = _resolve(root, input_path, "read")
    output = _resolve(root, output_path, "write")
    _require_pptx(source)
    if output.suffix.casefold() != ".pdf":
        raise ValueError("output_path must end with .pdf")

    pythoncom, app, presentation = _open_powerpoint(source)
    try:
        presentation.SaveAs(str(output), 32)
    finally:
        _close_powerpoint(pythoncom, app, presentation)
    if not output.is_file():
        raise RuntimeError("PowerPoint did not create the PDF")
    return {
        "status": "completed",
        "input_path": input_path,
        "output_path": output_path,
        "output_bytes": output.stat().st_size,
    }


def _fitz():
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is not installed in the Provider environment") from exc
    return fitz


@mcp.tool
def pdf_extract_images(
    root: str,
    input_path: str,
    output_dir: str,
    page_numbers: list[int] | None = None,
    min_width: int = 80,
    min_height: int = 80,
) -> dict[str, Any]:
    """Extract embedded raster images from selected PDF pages with PyMuPDF."""
    fitz = _fitz()
    source = _resolve(root, input_path, "read")
    _require_pdf(source)
    out_dir = _resolve(root, output_dir, "write")
    out_dir.mkdir(parents=True, exist_ok=True)
    if not (1 <= min_width <= 10000 and 1 <= min_height <= 10000):
        raise ValueError("minimum image dimensions are out of bounds")

    document = fitz.open(source)
    try:
        selected = (
            set(page_numbers)
            if page_numbers
            else set(range(1, document.page_count + 1))
        )
        outputs = []
        seen_xrefs = set()
        for page_no in sorted(selected):
            if not 1 <= page_no <= document.page_count:
                raise ValueError(f"invalid page number: {page_no}")
            page = document.load_page(page_no - 1)
            for image_index, image in enumerate(page.get_images(full=True), start=1):
                xref = int(image[0])
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                info = document.extract_image(xref)
                width = int(info.get("width", 0))
                height = int(info.get("height", 0))
                if width < min_width or height < min_height:
                    continue
                ext = str(info.get("ext") or "bin").casefold()
                filename = f"page_{page_no:03d}_image_{image_index:02d}_xref_{xref}.{ext}"
                target = out_dir / filename
                target.write_bytes(info["image"])
                outputs.append(
                    {
                        "page": page_no,
                        "xref": xref,
                        "width": width,
                        "height": height,
                        "path": str(PurePosixPath(output_dir) / filename),
                        "bytes": target.stat().st_size,
                    }
                )
        return {
            "status": "completed",
            "input_path": input_path,
            "output_dir": output_dir,
            "image_count": len(outputs),
            "images": outputs,
        }
    finally:
        document.close()


@mcp.tool
def pdf_render_region(
    root: str,
    input_path: str,
    page_number: int,
    output_path: str,
    clip: list[float] | None = None,
    dpi: int = 200,
) -> dict[str, Any]:
    """Render one full PDF page or clip rectangle to PNG with PyMuPDF."""
    fitz = _fitz()
    source = _resolve(root, input_path, "read")
    output = _resolve(root, output_path, "write")
    _require_pdf(source)
    if output.suffix.casefold() != ".png":
        raise ValueError("output_path must end with .png")
    if not 72 <= dpi <= 600:
        raise ValueError("dpi must be between 72 and 600")

    document = fitz.open(source)
    try:
        if not 1 <= page_number <= document.page_count:
            raise ValueError("page_number is out of range")
        page = document.load_page(page_number - 1)
        rect = None
        if clip is not None:
            if not isinstance(clip, list) or len(clip) != 4:
                raise ValueError("clip must be [x0, y0, x1, y1]")
            rect = fitz.Rect(*[float(value) for value in clip])
            if rect.is_empty or rect.is_infinite:
                raise ValueError("clip rectangle is invalid")
        scale = dpi / 72.0
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale),
            clip=rect,
            alpha=False,
        )
        pixmap.save(output)
        return {
            "status": "completed",
            "input_path": input_path,
            "page_number": page_number,
            "clip": clip,
            "dpi": dpi,
            "output_path": output_path,
            "width": pixmap.width,
            "height": pixmap.height,
            "output_bytes": output.stat().st_size,
        }
    finally:
        document.close()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
