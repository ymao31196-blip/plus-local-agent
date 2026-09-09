import hashlib
from pathlib import Path

import pytest
import changeset_manager as manager
import local_tools
from internal_tool_executor import execute_local_tool


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(local_tools, "WORKSPACE", root.resolve())
    return root


def prepare(root, count=2):
    changes = []
    for i in range(count):
        name = f"file{i}.txt"
        (root / name).write_bytes(b"old\n")
        changes.append({"path": name, "expected_sha256": hashlib.sha256(b"old\n").hexdigest(),
                        "patch": "@@ -1 +1 @@\n-old\n+new\n"})
    return changes


@pytest.mark.parametrize("count", [2, 3])
def test_success_and_hashes(workspace, count):
    result = local_tools.apply_changeset(prepare(workspace, count))
    assert result["status"] == "completed"
    assert result["files_requested"] == result["files_applied"] == count
    assert not result["rolled_back"]
    for item in result["results"]:
        assert item["before_sha256"] == hashlib.sha256(b"old\n").hexdigest()
        assert item["after_sha256"] == hashlib.sha256(b"new\n").hexdigest()
        assert (workspace / item["path"]).read_bytes() == b"new\n"
    assert not list(workspace.glob(".changeset-*"))


@pytest.mark.parametrize("bad_patch", [
    "@@ -1 +1 @@\n-wrong\n+new\n",
    "@@ -1 +99 @@\n-old\n+new\n",
    "@@ -5 +1 @@\n-old\n+new\n",
    "--- a/file1.txt\n+++ b/file1.txt\n@@ -1 +1 @@\n-old\n+new\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-x\n+y\n",
    "GIT binary patch\nliteral 0\n",
    "@@ -1 +1 @@\n-old\n+\x00\n",
    "--- /dev/null\n+++ b/file1.txt\n@@ -0,0 +1 @@\n+new\n",
    "--- a/file1.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n",
    "--- a/file1.txt\n+++ b/renamed.txt\n@@ -1 +1 @@\n-old\n+new\n",
    "x" * 1_000_001,
], ids=["context", "new-position", "old-position", "multi-diff", "binary", "nul", "create", "delete", "rename", "size"])
def test_second_patch_failure_changes_nothing(workspace, bad_patch):
    changes = prepare(workspace)
    changes[1]["patch"] = bad_patch
    result = local_tools.apply_changeset(changes)
    assert result["status"] == "error"
    assert result["failed_phase"] == "preflight"
    assert result["files_applied"] == 0
    assert all((workspace / c["path"]).read_bytes() == b"old\n" for c in changes)


def test_hash_conflict_zero_writes(workspace):
    changes = prepare(workspace)
    changes[1]["expected_sha256"] = "0" * 64
    result = execute_local_tool("apply_changeset", {"changes": changes})
    assert not result.ok
    assert result.error["type"] == "FileChangedSinceRead"
    assert result.result["files_applied"] == 0
    assert (workspace / "file0.txt").read_bytes() == b"old\n"


@pytest.mark.parametrize("mode", ["missing", "null", "invalid"])
def test_hash_required(workspace, mode):
    changes = prepare(workspace)
    if mode == "missing":
        del changes[1]["expected_sha256"]
    else:
        changes[1]["expected_sha256"] = None if mode == "null" else "invalid"
    result = local_tools.apply_changeset(changes)
    assert result["status"] == "error" and result["files_applied"] == 0


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_commit_failure_and_rollback_report(workspace, monkeypatch, rollback_fails):
    changes = prepare(workspace, 3)
    replace = manager.os.replace
    calls = 0
    def fail(source, target):
        nonlocal calls
        calls += 1
        if calls == 2 or (calls == 3 and rollback_fails):
            raise OSError("injected replacement failure")
        return replace(source, target)
    monkeypatch.setattr(manager.os, "replace", fail)
    result = local_tools.apply_changeset(changes)
    assert result["failed_phase"] == "commit"
    assert result["rollback_attempted"]
    assert result["rolled_back"] is not rollback_fails
    assert result["files_replaced_before_rollback"] == 1
    assert result["files_applied"] == int(rollback_fails)
    if rollback_fails:
        assert (workspace / result["recovery_files"][0]["backup_path"]).read_bytes() == b"old\n"
        assert result["rollback_errors"][0]["path"] == "file0.txt"
        assert result["unrestored_paths"] == ["file0.txt"]
        assert (workspace / "file0.txt").read_bytes() == b"new\n"
    else:
        assert result["restored_paths"] == ["file0.txt"]
        assert (workspace / "file0.txt").read_bytes() == b"old\n"
    assert (workspace / "file1.txt").read_bytes() == b"old\n"


@pytest.mark.parametrize("external", ["../outside.txt", "absolute", "symlink"])
def test_escape_paths(workspace, external):
    outside = workspace.parent / "outside.txt"
    outside.write_bytes(b"old\n")
    changes = prepare(workspace)
    if external == "absolute":
        external = str(outside)
    elif external == "symlink":
        link = workspace / "link.txt"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            import sys
            if sys.platform != "win32":
                raise
            # A Windows junction exercises the resolved reparse-point boundary
            # without requiring the symbolic-link privilege.
            import _winapi
            junction = workspace / "junction"
            _winapi.CreateJunction(str(outside.parent), str(junction))
            external = "junction/outside.txt"
        else:
            external = "link.txt"
    changes[1]["path"] = external
    result = local_tools.apply_changeset(changes)
    assert result["files_applied"] == 0
    assert result["error"]["type"] == "ValueError"
    assert outside.read_bytes() == b"old\n"


def test_invalid_utf8(workspace):
    changes = prepare(workspace)
    (workspace / "file1.txt").write_bytes(b"\xff")
    changes[1]["expected_sha256"] = hashlib.sha256(b"\xff").hexdigest()
    result = local_tools.apply_changeset(changes)
    assert result["error"]["type"] == "UnicodeDecodeError"
    assert (workspace / "file0.txt").read_bytes() == b"old\n"


@pytest.mark.parametrize("alias", ["file0.txt", "./file0.txt", "absolute"])
def test_duplicate_resolved_paths(workspace, alias):
    changes = prepare(workspace)
    changes[1]["path"] = str(workspace / "file0.txt") if alias == "absolute" else alias
    result = local_tools.apply_changeset(changes)
    assert result["files_applied"] == 0
    assert "Duplicate" in result["error"]["message"]


def test_staging_failure_zero_writes(workspace, monkeypatch):
    changes = prepare(workspace)
    stage = manager._stage
    calls = 0
    def fail(target, raw):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("staging failed")
        return stage(target, raw)
    monkeypatch.setattr(manager, "_stage", fail)
    result = local_tools.apply_changeset(changes)
    assert result["failed_phase"] == "stage"
    assert result["files_applied"] == 0
    assert not list(workspace.glob(".changeset-*"))
    assert (workspace / "file0.txt").read_bytes() == b"old\n"


def test_hash_rechecked_after_staging_before_any_commit(workspace, monkeypatch):
    changes = prepare(workspace)
    stage = manager._stage
    def intervene(target, raw):
        result = stage(target, raw)
        if target.name == "file1.txt":
            (workspace / "file0.txt").write_bytes(b"external\n")
        return result
    monkeypatch.setattr(manager, "_stage", intervene)
    result = local_tools.apply_changeset(changes)
    assert result["error"]["type"] == "FileChangedSinceRead"
    assert result["files_applied"] == result["files_replaced_before_rollback"] == 0
    assert (workspace / "file0.txt").read_bytes() == b"external\n"
    assert (workspace / "file1.txt").read_bytes() == b"old\n"


def test_file_count_and_total_patch_limit(workspace):
    changes = prepare(workspace, 33)
    assert local_tools.apply_changeset(changes)["files_applied"] == 0
    changes = prepare(workspace)
    for change in changes:
        change["patch"] = "@@ -1 +1 @@\n-old\n+" + "x" * 600_000 + "\n"
    result = local_tools.apply_changeset(changes)
    assert result["status"] == "error" and result["files_applied"] == 0


def test_missing_file_rejected(workspace):
    changes = prepare(workspace)
    changes[1]["path"] = "missing.txt"
    assert local_tools.apply_changeset(changes)["files_applied"] == 0
    assert not (workspace / "missing.txt").exists()


@pytest.mark.parametrize("original,patch,expected", [
    (b"", "@@ -0,0 +1 @@\n+new\n", b"new\n"),
    (b"old\n", "@@ -1 +0,0 @@\n-old\n", b""),
    (b"old\n", "@@ -1,0 +2 @@\n+new\n", b"old\nnew\n"),
    (b"old", "@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n", b"new\n"),
    (b"old\n", "@@ -1 +1 @@\n-old\n+new\n\\ No newline at end of file\n", b"new"),
    (b"old\r\n", "@@ -1 +1 @@\n-old\n+new\n", b"new\r\n"),
])
def test_exact_newline_and_zero_count_hunks(workspace, original, patch, expected):
    (workspace / "a.txt").write_bytes(original)
    result = local_tools.apply_changeset([{"path": "a.txt", "expected_sha256": hashlib.sha256(original).hexdigest(), "patch": patch}])
    assert result["status"] == "completed", result
    assert (workspace / "a.txt").read_bytes() == expected


def test_hardlink_duplicate(workspace):
    import os
    changes = prepare(workspace, 1)
    os.link(workspace / "file0.txt", workspace / "alias.txt")
    changes.append({**changes[0], "path": "alias.txt"})
    result = local_tools.apply_changeset(changes)
    assert result["status"] == "error"
    assert "Duplicate file" in result["error"]["message"]


def test_rollback_does_not_overwrite_external_edit(workspace, monkeypatch):
    changes = prepare(workspace)
    replace = manager.os.replace
    def intervene(source, target):
        if target.name == "file1.txt":
            (workspace / "file0.txt").write_bytes(b"external\n")
            raise OSError("commit failed")
        return replace(source, target)
    monkeypatch.setattr(manager.os, "replace", intervene)
    result = local_tools.apply_changeset(changes)
    assert result["rollback_errors"][0]["type"] == "FileChangedSinceRead"
    assert (workspace / "file0.txt").read_bytes() == b"external\n"
    assert result["recovery_files"]


def test_concurrent_changesets_serialize_hash_preconditions(workspace):
    from concurrent.futures import ThreadPoolExecutor
    changes = prepare(workspace)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: local_tools.apply_changeset(changes), range(2)))
    assert sorted(result["status"] for result in results) == ["completed", "error"]
    failure = next(result for result in results if result["status"] == "error")
    assert failure["error"]["type"] == "FileChangedSinceRead"
    assert failure["files_applied"] == 0


def test_manual_two_file_fixture_through_mcp(workspace):
    import asyncio
    import shutil
    from fastmcp import Client
    from server import mcp
    fixture = Path(__file__).resolve().parents[1] / "docs" / "fixtures" / "changeset_demo"
    shutil.copytree(fixture, workspace / "changeset_demo", ignore=shutil.ignore_patterns("__pycache__"))

    async def run():
        async with Client(mcp) as client:
            async def call(name, arguments):
                return (await client.call_tool_mcp(name, arguments)).structured_content
            before = await call("run_process", {"program": "pytest", "args": ["changeset_demo", "-q"]})
            assert before["returncode"] != 0 and "2 failed" in before["stdout"]
            changes = []
            for path, old, new in [("math_a.py", "a - b", "a + b"), ("math_b.py", "value + 2", "value * 2")]:
                path = "changeset_demo/" + path
                read = await call("read_text", {"path": path})
                changes.append({"path": path, "expected_sha256": read["sha256"],
                                "patch": f"@@ -2 +2 @@\n-    return {old}\n+    return {new}\n"})
            transaction = await call("apply_changeset", {"changes": changes})
            assert transaction["status"] == "completed"
            assert transaction["files_applied"] == 2
            after = await call("run_process", {"program": "pytest", "args": ["changeset_demo", "-q"]})
            assert after["returncode"] == 0 and "2 passed" in after["stdout"]
    asyncio.run(run())
