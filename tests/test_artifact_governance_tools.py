import pytest

import local_tools
import server


def test_artifact_gc_deletion_requires_exact_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())

    dry = server.artifact_gc()
    assert dry["dry_run"] is True

    with pytest.raises(PermissionError, match="confirmation='GC'"):
        server.artifact_gc(dry_run=False)

    applied = server.artifact_gc(dry_run=False, confirmation="GC")
    assert applied["dry_run"] is False


def test_artifact_verify_tool_reports_valid_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    (tmp_path / "sample.txt").write_text("hello", encoding="utf-8")
    metadata = server.internal_export_artifact("sample.txt")

    result = server.artifact_verify(metadata["artifact_id"])

    assert result["valid"] is True
    assert result["artifact_id"] == metadata["artifact_id"]
