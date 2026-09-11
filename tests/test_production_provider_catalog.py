import json
from pathlib import Path

from provider_manifest import load_provider_manifests


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_production_provider_catalog_matches_specs():
    manifests = load_provider_manifests(PROJECT_ROOT)
    manifest_ids = set(manifests)
    spec_ids = {
        path.stem
        for path in (PROJECT_ROOT / "provider_specs").glob("*.txt")
    }

    assert manifest_ids == {"docx", "markitdown", "pdf"}
    assert spec_ids == manifest_ids
    assert all(manifest.autostart for manifest in manifests.values())


def test_pdf_provider_exposes_only_reviewed_watermark_capability():
    manifests = load_provider_manifests(PROJECT_ROOT)
    pdf = manifests["pdf"]

    assert pdf.mode == "auto"
    assert pdf.tool_allowlist == ("add_text_watermark_direct",)
    assert "pdf_mcp.server" in " ".join(pdf.args)

    override = pdf.tool_overrides["add_text_watermark_direct"]
    contract = override["artifact_contract"]

    assert override["risk_level"] == "write_local"
    assert override["requires_confirmation"] is True
    assert contract["transport"] == "local_path"
    assert contract["inputs"] == ["input_path"]
    assert contract["outputs"] == []
    assert contract["output_paths"] == {
        "output_path": {
            "filename": "watermarked.pdf",
            "mime_type": "application/pdf",
        }
    }

    policy = contract["policy"]
    assert policy["max_input_bytes"] == 64 * 1024 * 1024
    assert policy["max_output_bytes"] == 64 * 1024 * 1024
    assert policy["max_output_artifacts"] == 1
    assert policy["allowed_input_mime_types"] == ["application/pdf"]
    assert policy["allowed_output_mime_types"] == ["application/pdf"]


def test_pdf_provider_spec_is_exactly_pinned():
    lines = [
        line.strip()
        for line in (PROJECT_ROOT / "provider_specs" / "pdf.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    assert "pdf-mcp-server==0.1.2" in lines
    assert "fastmcp==4.0.3" in lines
    assert "mcp==2.2.0" in lines
    assert "pikepdf==10.13.0.post1" in lines
    assert "PyMuPDF==1.28.2" in lines
    assert all("==" in line for line in lines)
