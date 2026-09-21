import hashlib
from pathlib import Path

import pytest

import artifact_bridge
import local_tools


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(local_tools, "WORKSPACE", root)
    return root


def test_export_creates_immutable_snapshot_with_metadata(workspace):
    source = workspace / "sample.txt"
    source.write_text("version one", encoding="utf-8")

    result = artifact_bridge.export_artifact("sample.txt", ttl_seconds=600)

    assert result["name"] == "sample.txt"
    assert result["mime_type"] == "text/plain"
    assert result["size"] == len(b"version one")
    assert result["sha256"] == hashlib.sha256(b"version one").hexdigest()
    assert result["source_root"] == "workspace"
    assert result["source_path"] == "sample.txt"
    assert artifact_bridge.read_artifact_bytes(result["artifact_id"]) == b"version one"

    source.write_text("version two", encoding="utf-8")
    assert artifact_bridge.read_artifact_bytes(result["artifact_id"]) == b"version one"


def test_materialize_artifact_creates_verified_persistent_copy(workspace):
    source = workspace / "source.bin"
    source.write_bytes(b"artifact payload")
    artifact = artifact_bridge.export_artifact("source.bin")

    result = artifact_bridge.materialize_artifact(
        artifact["artifact_id"],
        "downloads/persisted.bin",
    )

    target = workspace / "downloads" / "persisted.bin"
    assert target.read_bytes() == b"artifact payload"
    assert result["root"] == "workspace"
    assert result["path"] == "downloads/persisted.bin"
    assert result["size"] == len(b"artifact payload")
    assert result["sha256"] == artifact["sha256"]
    assert result["overwritten"] is False
    assert result["previous_sha256"] is None


def test_materialize_artifact_requires_hash_guard_for_overwrite(workspace):
    source = workspace / "source.bin"
    source.write_bytes(b"new payload")
    artifact = artifact_bridge.export_artifact("source.bin")
    target = workspace / "existing.bin"
    target.write_bytes(b"old payload")
    old_sha256 = hashlib.sha256(b"old payload").hexdigest()

    with pytest.raises(FileExistsError, match="already exists"):
        artifact_bridge.materialize_artifact(
            artifact["artifact_id"], "existing.bin",
        )
    with pytest.raises(PermissionError, match="expected_sha256"):
        artifact_bridge.materialize_artifact(
            artifact["artifact_id"], "existing.bin", overwrite=True,
        )
    with pytest.raises(local_tools.FileChangedSinceRead):
        artifact_bridge.materialize_artifact(
            artifact["artifact_id"],
            "existing.bin",
            overwrite=True,
            expected_sha256="0" * 64,
        )

    result = artifact_bridge.materialize_artifact(
        artifact["artifact_id"],
        "existing.bin",
        overwrite=True,
        expected_sha256=old_sha256,
    )

    assert target.read_bytes() == b"new payload"
    assert result["overwritten"] is True
    assert result["previous_sha256"] == old_sha256


def test_materialize_artifact_rejects_store_destination(workspace):
    source = workspace / "source.bin"
    source.write_bytes(b"payload")
    artifact = artifact_bridge.export_artifact("source.bin")

    with pytest.raises(ValueError, match="artifact store"):
        artifact_bridge.materialize_artifact(
            artifact["artifact_id"],
            f"{artifact_bridge.STORE_DIRNAME}/manual-copy.bin",
        )


def test_materialize_artifact_rechecks_source_integrity(workspace):
    source = workspace / "source.bin"
    source.write_bytes(b"payload")
    artifact = artifact_bridge.export_artifact("source.bin")
    payload = (
        workspace
        / artifact_bridge.STORE_DIRNAME
        / artifact["artifact_id"]
        / "payload"
    )
    payload.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="integrity"):
        artifact_bridge.materialize_artifact(
            artifact["artifact_id"], "copy.bin",
        )


def test_export_rejects_invalid_ttl_and_directories(workspace):
    (workspace / "folder").mkdir()
    with pytest.raises(ValueError, match="ttl_seconds"):
        artifact_bridge.export_artifact("folder", ttl_seconds=0)
    with pytest.raises(ValueError, match="Not a file"):
        artifact_bridge.export_artifact("folder")


def test_integrity_failure_is_detected(workspace):
    source = workspace / "sample.bin"
    source.write_bytes(b"abc")
    result = artifact_bridge.export_artifact("sample.bin")
    payload = workspace / artifact_bridge.STORE_DIRNAME / result["artifact_id"] / "payload"
    payload.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="integrity"):
        artifact_bridge.read_artifact_bytes(result["artifact_id"])


def test_revoke_removes_artifact(workspace):
    source = workspace / "sample.txt"
    source.write_text("hello", encoding="utf-8")
    result = artifact_bridge.export_artifact("sample.txt")

    revoked = artifact_bridge.revoke_artifact(result["artifact_id"])
    assert revoked == {"artifact_id": result["artifact_id"], "revoked": True}
    with pytest.raises(ValueError, match="not found or revoked"):
        artifact_bridge.read_artifact_bytes(result["artifact_id"])

    again = artifact_bridge.revoke_artifact(result["artifact_id"])
    assert again == {"artifact_id": result["artifact_id"], "revoked": False}


def test_chunked_export_reassembles_exact_bytes(workspace):
    payload = (b"0123456789abcdef" * 1000) + b"tail"
    source = workspace / "large.bin"
    source.write_bytes(payload)

    result = artifact_bridge.prepare_chunked_artifact(
        "large.bin", chunk_bytes=4096,
    )

    assert result["part_count"] == 4
    assert result["chunk_bytes"] == 4096
    chunks = [
        artifact_bridge.read_artifact_chunk(result["artifact_id"], index)
        for index in range(result["part_count"])
    ]
    rebuilt = b"".join(chunks)
    assert rebuilt == payload
    assert hashlib.sha256(rebuilt).hexdigest() == result["sha256"]

    with pytest.raises(ValueError, match="part_index"):
        artifact_bridge.read_artifact_chunk(result["artifact_id"], result["part_count"])


def test_chunked_export_enforces_max_chunk_size(workspace):
    source = workspace / "large.bin"
    source.write_bytes(b"x")
    with pytest.raises(ValueError, match="chunk_bytes"):
        artifact_bridge.prepare_chunked_artifact(
            "large.bin", chunk_bytes=artifact_bridge.MAX_CHUNK_BYTES + 1,
        )

def test_internal_artifact_snapshots_parent_provenance(workspace):
    parent_source = workspace / "parent.txt"
    parent_source.write_text("parent", encoding="utf-8")
    parent = artifact_bridge.export_artifact("parent.txt")

    provider_output = workspace / "provider-output.txt"
    provider_output.write_text("child", encoding="utf-8")
    child = artifact_bridge.import_internal_artifact(
        provider_output,
        source_provider="demo",
        source_capability="demo.convert",
        parent_artifacts=[parent["artifact_id"]],
    )

    metadata = artifact_bridge.artifact_metadata(child["artifact_id"])
    assert metadata["parent_artifacts"] == [parent["artifact_id"]]
    assert metadata["provenance"]["source_provider"] == "demo"
    assert metadata["provenance"]["source_capability"] == "demo.convert"
    assert metadata["provenance"]["parents"] == [{
        "artifact_id": parent["artifact_id"],
        "sha256": parent["sha256"],
        "size": parent["size"],
        "name": parent["name"],
        "mime_type": parent["mime_type"],
    }]
    report = artifact_bridge.verify_artifact_provenance(
        child["artifact_id"]
    )
    assert report["valid"] is True
    assert report["checked_artifacts"] == 2


def test_provenance_detects_parent_metadata_tampering(workspace):
    parent_source = workspace / "parent.txt"
    parent_source.write_text("parent", encoding="utf-8")
    parent = artifact_bridge.export_artifact("parent.txt")

    output = workspace / "child.txt"
    output.write_text("child", encoding="utf-8")
    child = artifact_bridge.import_internal_artifact(
        output,
        parent_artifacts=[parent["artifact_id"]],
    )

    parent_meta_path = (
        workspace
        / artifact_bridge.STORE_DIRNAME
        / parent["artifact_id"]
        / "metadata.json"
    )
    metadata = __import__("json").loads(
        parent_meta_path.read_text(encoding="utf-8")
    )
    metadata["name"] = "tampered.txt"
    parent_meta_path.write_text(
        __import__("json").dumps(metadata),
        encoding="utf-8",
    )

    report = artifact_bridge.verify_artifact_provenance(
        child["artifact_id"]
    )
    assert report["valid"] is False
    assert any(
        item["type"] == "parent_snapshot_mismatch"
        and item.get("field") == "name"
        for item in report["issues"]
    )


def test_gc_removes_unreferenced_expired_artifact(workspace):
    source = workspace / "old.txt"
    source.write_text("old", encoding="utf-8")
    artifact = artifact_bridge.export_artifact("old.txt")

    meta_path = (
        workspace
        / artifact_bridge.STORE_DIRNAME
        / artifact["artifact_id"]
        / "metadata.json"
    )
    metadata = __import__("json").loads(
        meta_path.read_text(encoding="utf-8")
    )
    metadata["expires_at"] = "2000-01-01T00:00:00+00:00"
    meta_path.write_text(
        __import__("json").dumps(metadata),
        encoding="utf-8",
    )

    dry = artifact_bridge.artifact_gc(dry_run=True)
    assert artifact["artifact_id"] in dry["candidates"]
    assert dry["removed_count"] == 0

    applied = artifact_bridge.artifact_gc(dry_run=False)
    assert applied["removed_count"] == 1
    assert not (
        workspace
        / artifact_bridge.STORE_DIRNAME
        / artifact["artifact_id"]
    ).exists()


def test_gc_preserves_expired_ancestor_of_live_child(workspace):
    source = workspace / "parent.txt"
    source.write_text("parent", encoding="utf-8")
    parent = artifact_bridge.export_artifact("parent.txt")

    output = workspace / "child.txt"
    output.write_text("child", encoding="utf-8")
    child = artifact_bridge.import_internal_artifact(
        output,
        parent_artifacts=[parent["artifact_id"]],
    )

    parent_meta_path = (
        workspace
        / artifact_bridge.STORE_DIRNAME
        / parent["artifact_id"]
        / "metadata.json"
    )
    parent_meta = __import__("json").loads(
        parent_meta_path.read_text(encoding="utf-8")
    )
    parent_meta["expires_at"] = "2000-01-01T00:00:00+00:00"
    parent_meta_path.write_text(
        __import__("json").dumps(parent_meta),
        encoding="utf-8",
    )

    report = artifact_bridge.artifact_gc(dry_run=True)
    assert report["protected_ancestor_count"] == 1
    assert parent["artifact_id"] not in report["candidates"]
    assert (
        workspace
        / artifact_bridge.STORE_DIRNAME
        / parent["artifact_id"]
    ).exists()
    assert artifact_bridge.read_artifact_bytes(
        child["artifact_id"]
    ) == b"child"


def test_size_metadata_tampering_is_detected(workspace):
    source = workspace / "sample.bin"
    source.write_bytes(b"abc")
    artifact = artifact_bridge.export_artifact("sample.bin")

    meta_path = (
        workspace
        / artifact_bridge.STORE_DIRNAME
        / artifact["artifact_id"]
        / "metadata.json"
    )
    metadata = __import__("json").loads(
        meta_path.read_text(encoding="utf-8")
    )
    metadata["size"] = 999
    meta_path.write_text(
        __import__("json").dumps(metadata),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="size integrity"):
        artifact_bridge.read_artifact_bytes(artifact["artifact_id"])
