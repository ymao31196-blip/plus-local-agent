"""Safety and E2E tests for structured file/ZIP operations."""
from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path

import pytest

from host.workspace_manager import RootDefinition, build_root_policy
from tooling import local_tools
from tooling.file_operations import file_operation
from tooling.internal_tool_executor import (
    ACTION_LOCAL_TOOLS, INTERNAL_TOOL_SCHEMAS,
    execute_actions_request, execute_local_tool,
)


@pytest.fixture
def space(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    repository = tmp_path / "source"
    workspace.mkdir()
    repository.mkdir()
    monkeypatch.setattr(local_tools, "WORKSPACE", workspace.resolve())
    monkeypatch.setattr(local_tools, "PLA_ROOT", repository.resolve())
    return workspace


def test_directory_creation_and_batch(space):
    (space / "src.txt").write_text("alpha", encoding="utf-8")
    assert "file_operation" in ACTION_LOCAL_TOOLS
    assert any(item["name"] == "file_operation" for item in INTERNAL_TOOL_SCHEMAS)
    result = execute_actions_request([
        {"tool": "file_operation", "arguments": {
            "operation": "mkdir", "destination": "assets/images"}},
        {"tool": "file_operation", "arguments": {
            "operation": "copy", "source": "src.txt", "destination": "assets/images/a.txt"}},
        {"tool": "file_operation", "arguments": {
            "operation": "rename", "source": "assets/images/a.txt",
            "destination": "assets/images/renamed.txt"}},
    ])
    assert result["status"] == "completed"
    assert result["actions_executed"] == 3
    assert (space / "assets/images/renamed.txt").read_text(encoding="utf-8") == "alpha"
    assert file_operation("mkdir", "assets/images")["status"] == "completed"


def test_copy_file_explicit_overwrite(space):
    (space / "source.txt").write_bytes(b"new")
    (space / "dest.txt").write_bytes(b"old")
    with pytest.raises(FileExistsError):
        file_operation("copy", "dest.txt", "source.txt")
    assert (space / "dest.txt").read_bytes() == b"old"
    result = file_operation("copy", "dest.txt", "source.txt", overwrite=True)
    assert result["entries"] == 1
    assert (space / "dest.txt").read_bytes() == b"new"


def test_copy_directory_and_reject_inside_itself(space):
    (space / "folder").mkdir()
    (space / "folder/a.txt").write_text("a", encoding="utf-8")
    file_operation("copy", "copy", "folder")
    assert (space / "copy/a.txt").read_text(encoding="utf-8") == "a"
    with pytest.raises(ValueError, match="overlaps"):
        file_operation("copy", "folder/nested", "folder")


def test_move_and_rename_file(space):
    (space / "x.txt").write_text("x", encoding="utf-8")
    (space / "dest").mkdir()
    file_operation("move", "dest/x.txt", "x.txt")
    assert not (space / "x.txt").exists()
    file_operation("rename", "dest/y.txt", "dest/x.txt")
    assert (space / "dest/y.txt").exists()
    with pytest.raises(ValueError, match="inside its source directory"):
        file_operation("rename", "other.txt", "dest/y.txt")


def test_move_overwrite_regular_file_only(space):
    (space / "a.txt").write_text("a", encoding="utf-8")
    (space / "b.txt").write_text("b", encoding="utf-8")
    file_operation("move", "b.txt", "a.txt", overwrite=True)
    assert (space / "b.txt").read_text(encoding="utf-8") == "a"
    assert not (space / "a.txt").exists()
    (space / "c").mkdir()
    with pytest.raises(FileExistsError):
        file_operation("copy", "c", "b.txt", overwrite=True)


def test_cross_root_read_write_authorization(space, tmp_path, monkeypatch):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "remote.txt").write_text("remote", encoding="utf-8")
    policy = build_root_policy(
        space, tmp_path / "source",
        {"foreign": RootDefinition("foreign", foreign, read=True, write=False, execute=False)},
    )
    monkeypatch.setattr(local_tools, "root_policy", lambda _root=None: policy)
    file_operation("copy", "remote.txt", "remote.txt", root="foreign",
                   destination_root="workspace")
    assert (space / "remote.txt").read_text(encoding="utf-8") == "remote"
    with pytest.raises(ValueError, match="write access"):
        file_operation("move", "no.txt", "remote.txt", root="foreign",
                       destination_root="workspace")
    with pytest.raises(ValueError, match="write access"):
        file_operation("copy", "new.txt", "remote.txt", root="workspace",
                       destination_root="foreign")


def test_reject_outside_root(space):
    (space / "safe.txt").write_text("yes", encoding="utf-8")
    with pytest.raises(ValueError, match="outside workspace"):
        file_operation("copy", "../escaped.txt", "safe.txt")
    with pytest.raises(ValueError, match="outside workspace"):
        file_operation("mkdir", "../outside/dir")


def test_reject_symlink_in_directory_before_copy(space, tmp_path):
    folder = space / "folder"
    folder.mkdir()
    (folder / "safe.txt").write_text("ok", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("no", encoding="utf-8")
    try:
        (folder / "link.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink unavailable on this host")
    with pytest.raises(ValueError, match="outside|Symlink"):
        file_operation("copy", "copied", "folder")
    assert not (space / "copied").exists()


def test_reject_private_pla_path_in_archive(space, tmp_path):
    pla = tmp_path / "source"
    (pla / ".git").mkdir()
    (pla / ".git/config").write_text("private", encoding="utf-8")
    with pytest.raises(ValueError, match="Protected path"):
        file_operation("archive_create", "backup.zip", sources=["."],
                       root="pla", destination_root="workspace")
    assert not (space / "backup.zip").exists()


def test_zip_roundtrip_and_nested_folders(space):
    (space / "docs/nested").mkdir(parents=True)
    (space / "docs/nested/a.txt").write_text("abcdef", encoding="utf-8")
    (space / "docs/empty").mkdir()
    zipped = file_operation("archive_create", "bundle.zip", sources=["docs"])
    assert zipped["entries"] == 1
    result = file_operation("archive_extract", "out", "bundle.zip")
    assert result["status"] == "completed"
    assert (space / "out/docs/nested/a.txt").read_text(encoding="utf-8") == "abcdef"
    assert (space / "out/docs/empty").is_dir()


@pytest.mark.parametrize("name", [
    "../escape.txt", "/absolute.txt", "C:/drive.txt", r"..\escape.txt",
])
def test_zip_rejects_path_traversal_before_writing(space, name):
    with zipfile.ZipFile(space / "evil.zip", "w") as zf:
        zf.writestr("safe.txt", "safe")
        zf.writestr(name, "bad")
    with pytest.raises(ValueError, match="Unsafe ZIP"):
        file_operation("archive_extract", "out", "evil.zip")
    assert not (space / "out").exists()


def test_zip_rejects_symlink_entry(space):
    entry = zipfile.ZipInfo("sym")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(space / "sym.zip", "w") as zf:
        zf.writestr(entry, "other")
    with pytest.raises(ValueError, match="symlink"):
        file_operation("archive_extract", "out", "sym.zip")


def test_zip_rejects_existing_target_unless_overwrite(space):
    with zipfile.ZipFile(space / "ok.zip", "w") as zf:
        zf.writestr("a.txt", "updated")
    (space / "out").mkdir()
    (space / "out/a.txt").write_text("original", encoding="utf-8")
    with pytest.raises(FileExistsError):
        file_operation("archive_extract", "out", "ok.zip")
    assert (space / "out/a.txt").read_text(encoding="utf-8") == "original"
    file_operation("archive_extract", "out", "ok.zip", overwrite=True)
    assert (space / "out/a.txt").read_text(encoding="utf-8") == "updated"


def test_zip_rejects_duplicate_entries(space):
    with zipfile.ZipFile(space / "dup.zip", "w") as zf:
        zf.writestr("One.txt", "one")
        zf.writestr("one.txt", "two")
    with pytest.raises(ValueError, match="Duplicate"):
        file_operation("archive_extract", "out", "dup.zip")
    assert not (space / "out").exists()


def test_zip_rejects_high_decompression_ratio(space):
    with zipfile.ZipFile(space / "bomb.zip", "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("oversized.txt", b"A" * 1024 * 1024)
    with pytest.raises(ValueError, match="limits"):
        file_operation("archive_extract", "out", "bomb.zip")


def test_zip_rejects_duplicate_archive_sources(space):
    (space / "a").mkdir()
    (space / "a/file.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        file_operation("archive_create", "out.zip", sources=["a", "a"])
    assert not (space / "out.zip").exists()


def test_batch_stops_on_failure_preserves_successful_steps(space):
    (space / "in.txt").write_text("hello", encoding="utf-8")
    result = execute_actions_request([
        {"tool": "file_operation", "arguments": {
            "operation": "mkdir", "destination": "output"}},
        {"tool": "file_operation", "arguments": {
            "operation": "copy", "source": "missing.txt", "destination": "output/o.txt"}},
        {"tool": "file_operation", "arguments": {
            "operation": "move", "source": "in.txt", "destination": "output/in.txt"}},
    ])
    assert result["status"] == "partial"
    assert result["actions_executed"] == 2
    assert (space / "output").is_dir()
    assert (space / "in.txt").exists()
    assert result["results"][1]["error"]["type"] == "ValueError"


def test_schema_registers_single_structured_operation():
    from capabilities.core_capabilities import core_local_tool_descriptors
    descriptors = {entry.id: entry for entry in core_local_tool_descriptors()}
    assert "core.file_operation" in descriptors
    assert descriptors["core.file_operation"].risk_level == "write_local"
    schema = descriptors["core.file_operation"].input_schema
    assert "archive_extract" in schema["properties"]["operation"]["enum"]


def test_core_capability_is_discoverable_in_chinese():
    from capabilities.core_capabilities import core_local_tool_descriptors
    from capabilities.capability_registry import CapabilityRegistry
    registry = CapabilityRegistry()
    registry.register_provider("core", core_local_tool_descriptors(), enabled=True)
    result = registry.search("文件整理", provider_id="core")
    assert any(item["id"] == "core.file_operation" for item in result["capabilities"])


def test_copy_to_write_only_root(space, tmp_path, monkeypatch):
    destination = tmp_path / "write_only"
    destination.mkdir()
    (space / "source.txt").write_text("x", encoding="utf-8")
    policy = build_root_policy(
        space, tmp_path / "source",
        {"writeonly": RootDefinition(
            "writeonly", destination, read=False, write=True, execute=False,
        )},
    )
    monkeypatch.setattr(local_tools, "root_policy", lambda _root=None: policy)
    result = file_operation(
        "copy", "copied.txt", "source.txt",
        root="workspace", destination_root="writeonly",
    )
    assert result["destination"] == "copied.txt"
    assert result["destination_root"] == "writeonly"
    assert (destination / "copied.txt").read_text(encoding="utf-8") == "x"


@pytest.mark.parametrize("name", ["CON.txt", "folder/NUL", "trailing.", "a//b"])
def test_zip_rejects_windows_unsafe_names(space, name):
    with zipfile.ZipFile(space / "unsafe.zip", "w") as zf:
        zf.writestr(name, "bad")
    with pytest.raises(ValueError, match="Unsafe ZIP"):
        file_operation("archive_extract", "out", "unsafe.zip")
    assert not (space / "out").exists()


def test_batch_can_continue_after_failed_operation(space):
    result = execute_actions_request([
        {"tool": "file_operation", "arguments": {
            "operation": "copy", "destination": "missing.txt",
            "source": "missing_source.txt"}},
        {"tool": "file_operation", "arguments": {
            "operation": "mkdir", "destination": "after"}},
    ], stop_on_error=False)
    assert result["actions_executed"] == 2
    assert result["status"] == "partial"
    assert (space / "after").is_dir()
