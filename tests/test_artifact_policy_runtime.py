import pytest

import artifact_bridge
import local_tools
from artifact_runtime import ArtifactInvocation


def test_input_size_policy_blocks_before_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    (tmp_path / "sample.bin").write_bytes(b"12345")
    source = artifact_bridge.export_artifact("sample.bin")

    with ArtifactInvocation(
        "provider",
        "provider.read",
        policy={"max_input_bytes": 4},
    ) as invocation:
        with pytest.raises(ValueError, match="max_input_bytes"):
            invocation.stage_input(source["artifact_id"], "input")


def test_input_mime_policy_blocks_disallowed_type(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    (tmp_path / "sample.pdf").write_bytes(b"%PDF-1.4\n")
    source = artifact_bridge.export_artifact("sample.pdf")

    with ArtifactInvocation(
        "provider",
        "provider.read",
        policy={"allowed_input_mime_types": ["text/*"]},
    ) as invocation:
        with pytest.raises(ValueError, match="MIME type"):
            invocation.stage_input(source["artifact_id"], "input")


def test_output_size_and_mime_policy_blocks_before_import(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())

    with ArtifactInvocation(
        "provider",
        "provider.write",
        policy={
            "max_output_bytes": 4,
            "max_total_output_bytes": 4,
            "allowed_output_mime_types": ["text/plain"],
        },
    ) as invocation:
        output = invocation.root / "outputs" / "result.bin"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"12345")
        with pytest.raises(ValueError, match="max_output_bytes"):
            invocation.import_output(
                str(output),
                mime_type="text/plain",
            )

    with ArtifactInvocation(
        "provider",
        "provider.write",
        policy={"allowed_output_mime_types": ["text/plain"]},
    ) as invocation:
        output = invocation.root / "outputs" / "result.pdf"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"%PDF-1.4")
        with pytest.raises(ValueError, match="MIME type"):
            invocation.import_output(
                str(output),
                mime_type="application/pdf",
            )


def test_output_count_and_total_size_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())

    with ArtifactInvocation(
        "provider",
        "provider.write",
        policy={
            "max_output_bytes": 5,
            "max_total_output_bytes": 6,
            "max_output_artifacts": 2,
        },
    ) as invocation:
        one = invocation.root / "outputs" / "one.txt"
        two = invocation.root / "outputs" / "two.txt"
        one.parent.mkdir(parents=True, exist_ok=True)
        one.write_bytes(b"1234")
        two.write_bytes(b"5678")
        invocation.import_output(str(one), mime_type="text/plain")
        with pytest.raises(ValueError, match="max_total_output_bytes"):
            invocation.import_output(str(two), mime_type="text/plain")


def test_output_ttl_cannot_exceed_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())

    with ArtifactInvocation(
        "provider",
        "provider.write",
        policy={"output_ttl_seconds": 30},
    ) as invocation:
        output = invocation.root / "outputs" / "result.txt"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("x", encoding="utf-8")
        with pytest.raises(ValueError, match="TTL"):
            invocation.import_output(
                str(output),
                mime_type="text/plain",
                ttl_seconds=31,
            )
