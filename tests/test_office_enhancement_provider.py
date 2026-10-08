import zipfile
from pathlib import Path

from lxml import etree

from providers import office_enhancement_server as office
from provider.provider_manifest import load_provider_manifests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


def _content_types() -> bytes:
    root = etree.Element(f"{{{CT_NS}}}Types", nsmap={None: CT_NS})
    default = etree.SubElement(root, f"{{{CT_NS}}}Default")
    default.set("Extension", "xml")
    default.set("ContentType", "application/xml")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def _presentation() -> bytes:
    root = etree.Element(f"{{{P_NS}}}presentation", nsmap={"p": P_NS})
    size = etree.SubElement(root, f"{{{P_NS}}}sldSz")
    size.set("cx", "12192000")
    size.set("cy", "6858000")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def _shape_tree(text: str, *, with_picture: bool = False) -> bytes:
    nsmap = {"p": P_NS, "a": A_NS, "r": R_NS}
    slide = etree.Element(f"{{{P_NS}}}sld", nsmap=nsmap)
    c_sld = etree.SubElement(slide, f"{{{P_NS}}}cSld")
    tree = etree.SubElement(c_sld, f"{{{P_NS}}}spTree")
    etree.SubElement(tree, f"{{{P_NS}}}nvGrpSpPr")
    etree.SubElement(tree, f"{{{P_NS}}}grpSpPr")

    shape = etree.SubElement(tree, f"{{{P_NS}}}sp")
    nv = etree.SubElement(shape, f"{{{P_NS}}}nvSpPr")
    c_nv = etree.SubElement(nv, f"{{{P_NS}}}cNvPr")
    c_nv.set("id", "2")
    c_nv.set("name", text)
    etree.SubElement(nv, f"{{{P_NS}}}cNvSpPr")
    etree.SubElement(nv, f"{{{P_NS}}}nvPr")
    etree.SubElement(shape, f"{{{P_NS}}}spPr")
    tx = etree.SubElement(shape, f"{{{P_NS}}}txBody")
    etree.SubElement(tx, f"{{{A_NS}}}bodyPr")
    etree.SubElement(tx, f"{{{A_NS}}}lstStyle")
    p = etree.SubElement(tx, f"{{{A_NS}}}p")
    run = etree.SubElement(p, f"{{{A_NS}}}r")
    t = etree.SubElement(run, f"{{{A_NS}}}t")
    t.text = text

    if with_picture:
        pic = etree.SubElement(tree, f"{{{P_NS}}}pic")
        pic_nv = etree.SubElement(pic, f"{{{P_NS}}}nvPicPr")
        pic_c_nv = etree.SubElement(pic_nv, f"{{{P_NS}}}cNvPr")
        pic_c_nv.set("id", "3")
        pic_c_nv.set("name", "overlay-image")
        etree.SubElement(pic_nv, f"{{{P_NS}}}cNvPicPr")
        etree.SubElement(pic_nv, f"{{{P_NS}}}nvPr")
        blip_fill = etree.SubElement(pic, f"{{{P_NS}}}blipFill")
        blip = etree.SubElement(blip_fill, f"{{{A_NS}}}blip")
        blip.set(f"{{{R_NS}}}embed", "rId2")
        etree.SubElement(pic, f"{{{P_NS}}}spPr")

    return etree.tostring(slide, xml_declaration=True, encoding="UTF-8")


def _rels(*, overlay: bool) -> bytes:
    root = etree.Element(f"{{{PKG_REL_NS}}}Relationships", nsmap={None: PKG_REL_NS})
    layout = etree.SubElement(root, f"{{{PKG_REL_NS}}}Relationship")
    layout.set("Id", "rId1")
    layout.set(
        "Type",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout",
    )
    layout.set("Target", "../slideLayouts/slideLayout1.xml")
    if overlay:
        image = etree.SubElement(root, f"{{{PKG_REL_NS}}}Relationship")
        image.set("Id", "rId2")
        image.set(
            "Type",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
        )
        image.set("Target", "/ppt/media/image.png")

        notes = etree.SubElement(root, f"{{{PKG_REL_NS}}}Relationship")
        notes.set("Id", "rId3")
        notes.set(
            "Type",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide",
        )
        notes.set("Target", "/ppt/notesSlides/notesSlide1.xml")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def _write_fixture(path: Path, *, overlay: bool) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types())
        zf.writestr("ppt/presentation.xml", _presentation())
        zf.writestr(
            "ppt/slides/slide1.xml",
            _shape_tree("Overlay" if overlay else "Base", with_picture=overlay),
        )
        zf.writestr("ppt/slides/_rels/slide1.xml.rels", _rels(overlay=overlay))
        if overlay:
            zf.writestr("ppt/media/image.png", b"\x89PNG\r\nfixture")


def test_office_enhancement_manifest_is_reviewed_and_bounded():
    manifests = load_provider_manifests(PROJECT_ROOT)
    provider = manifests["office-enhancement"]

    assert provider.runtime_kind == "isolated_python_stdio"
    assert provider.routing_authority == "preferred"
    assert set(provider.tool_allowlist or ()) == {
        "office_enhancement_status",
        "artifact_build_overlay",
        "pptx_inspect",
        "pptx_merge_overlay",
        "powerpoint_inspect",
        "powerpoint_render_slide",
        "powerpoint_render_pdf",
        "pdf_extract_images",
        "pdf_render_region",
    }
    assert provider.tool_overrides["office_enhancement_status"]["risk_level"] == "read"
    assert provider.tool_overrides["powerpoint_inspect"]["risk_level"] == "read"
    assert provider.tool_overrides["pptx_merge_overlay"]["risk_level"] == "write_local"
    assert provider.tool_overrides["artifact_build_overlay"]["requires_confirmation"] is False


def test_overlay_spec_is_structured_and_rejects_forward_connector(monkeypatch, tmp_path):
    monkeypatch.setattr(
        office,
        "_resolve",
        lambda _root, path, _mode: (tmp_path / path).resolve(),
    )

    spec = {
        "elements": [
            {"kind": "connector", "from": "a", "to": "b"},
            {
                "kind": "shape",
                "id": "a",
                "position": {"left": 0, "top": 0, "width": 100, "height": 100},
            },
        ]
    }
    try:
        office._normalize_overlay_spec("workspace", spec)
    except ValueError as exc:
        assert "earlier" in str(exc)
    else:
        raise AssertionError("forward connector must be rejected")


def test_pptx_inspect_and_merge_only_referenced_relationships(monkeypatch, tmp_path):
    base = tmp_path / "base.pptx"
    overlay = tmp_path / "overlay.pptx"
    merged = tmp_path / "merged.pptx"
    _write_fixture(base, overlay=False)
    _write_fixture(overlay, overlay=True)

    monkeypatch.setattr(
        office,
        "_resolve",
        lambda _root, path, _mode: (tmp_path / path).resolve(),
    )

    inspected = office.pptx_inspect("workspace", "base.pptx")
    assert inspected["slide_count"] == 1
    assert inspected["slides"][0]["text_preview"] == "Base"

    result = office.pptx_merge_overlay(
        "workspace",
        "base.pptx",
        "overlay.pptx",
        1,
        "merged.pptx",
        1,
    )
    assert result["elements_added"] == 2
    assert result["media_copied"] == 1

    with zipfile.ZipFile(merged, "r") as zf:
        slide = etree.fromstring(zf.read("ppt/slides/slide1.xml"))
        texts = slide.xpath(".//a:t/text()", namespaces={"a": A_NS})
        assert texts == ["Base", "Overlay"]

        rels = etree.fromstring(zf.read("ppt/slides/_rels/slide1.xml.rels"))
        relation_types = [
            rel.get("Type", "")
            for rel in rels.findall(f"{{{PKG_REL_NS}}}Relationship")
        ]
        assert any(value.endswith("/image") for value in relation_types)
        assert not any(value.endswith("/notesSlide") for value in relation_types)
        assert "ppt/media/officeEnhancement_1.png" in zf.namelist()

        content_types = etree.fromstring(zf.read("[Content_Types].xml"))
        defaults = {
            node.get("Extension"): node.get("ContentType")
            for node in content_types.findall(f"{{{CT_NS}}}Default")
        }
        assert defaults["png"] == "image/png"
