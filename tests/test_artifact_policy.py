import pytest

from artifact_policy import (
    HARD_MAX_INPUT_BYTES,
    mime_matches,
    normalize_artifact_policy,
    normalize_mime_type,
    validate_artifact_content,
)


def test_default_policy_is_bounded():
    policy = normalize_artifact_policy({})
    assert policy["max_input_bytes"] > 0
    assert policy["max_output_bytes"] > 0
    assert policy["max_total_output_bytes"] >= policy["max_output_bytes"]
    assert policy["max_output_artifacts"] > 0
    assert policy["output_ttl_seconds"] <= 3600


def test_policy_rejects_values_above_hard_caps():
    with pytest.raises(ValueError, match="max_input_bytes"):
        normalize_artifact_policy({
            "max_input_bytes": HARD_MAX_INPUT_BYTES + 1,
        })


def test_policy_rejects_total_smaller_than_single_output():
    with pytest.raises(ValueError, match="max_total_output_bytes"):
        normalize_artifact_policy({
            "max_output_bytes": 100,
            "max_total_output_bytes": 99,
        })


def test_mime_normalization_and_wildcard_matching():
    assert normalize_mime_type("Text/Plain") == "text/plain"
    assert mime_matches("text/markdown", ["text/*"])
    assert not mime_matches("application/pdf", ["text/*"])


def test_policy_rejects_invalid_and_duplicate_mime_entries():
    with pytest.raises(ValueError, match="Invalid MIME"):
        normalize_artifact_policy({
            "allowed_input_mime_types": ["not-a-mime"],
        })
    with pytest.raises(ValueError, match="duplicate"):
        normalize_artifact_policy({
            "allowed_output_mime_types": [
                "application/pdf",
                "Application/PDF",
            ],
        })


def test_content_signature_validation_for_text_pdf_and_docx(tmp_path):
    text_file = tmp_path / "sample.md"
    text_file.write_text("# hello", encoding="utf-8")
    validate_artifact_content(text_file, "text/markdown")

    binary_text = tmp_path / "binary.txt"
    binary_text.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ValueError, match="does not match MIME"):
        validate_artifact_content(binary_text, "text/plain")

    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    validate_artifact_content(pdf, "application/pdf")

    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"not pdf")
    with pytest.raises(ValueError, match="does not match MIME"):
        validate_artifact_content(fake_pdf, "application/pdf")


def test_docx_signature_requires_office_container_members(tmp_path):
    import zipfile

    docx = tmp_path / "good.docx"
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
    validate_artifact_content(
        docx,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    bad = tmp_path / "bad.docx"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("random.txt", "x")
    with pytest.raises(ValueError, match="does not match MIME"):
        validate_artifact_content(
            bad,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
