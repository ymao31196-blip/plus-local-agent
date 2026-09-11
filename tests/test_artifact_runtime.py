from pathlib import Path

import artifact_bridge
import local_tools
from artifact_runtime import ArtifactInvocation


def test_file_uri_staging_and_embedded_reference_sanitization(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    (tmp_path / "sample.txt").write_text("hello", encoding="utf-8")
    source = artifact_bridge.export_artifact("sample.txt")
    source_id = source["artifact_id"]

    with ArtifactInvocation("provider", "provider.convert") as invocation:
        staged_uri = invocation.stage_input(
            source_id,
            "uri",
            transport="file_uri",
        )
        assert staged_uri.startswith("file:")
        sanitized = invocation.sanitize(f"opened {staged_uri} successfully")
        assert source_id in sanitized
        assert ".capability_io" not in sanitized

    io_root = tmp_path / ".capability_io"
    assert not any(io_root.iterdir()) if io_root.exists() else True


def test_sanitizer_replaces_repeatedly_escaped_windows_output_path(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())

    with ArtifactInvocation("provider", "provider.create") as invocation:
        output_path = Path(invocation.allocate_output("output_path", "result.txt"))
        output_path.write_text("result", encoding="utf-8")
        metadata = invocation.import_output(
            str(output_path),
            mime_type="text/plain",
        )
        artifact_id = metadata["artifact_id"]

        escaped = str(output_path.resolve())
        for _ in range(3):
            escaped = escaped.replace("\\", "\\\\")
        value = f'provider result path={escaped}'
        sanitized = invocation.sanitize(value)

        assert artifact_id in sanitized
        assert ".capability_io" not in sanitized
