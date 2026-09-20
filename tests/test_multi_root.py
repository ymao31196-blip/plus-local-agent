import hashlib
from pathlib import Path
import sys
import time

import pytest

import local_tools
from internal_tool_executor import INTERNAL_TOOL_SCHEMAS, execute_actions_request, execute_local_tool
from task_store import TERMINAL, TaskStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_workspace_config(pla: Path, roots: dict[str, Path]) -> None:
    config = pla / "config" / "workspaces.local.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    lines = ["version: 1", "roots:"]
    for name, path in roots.items():
        escaped = str(path.resolve()).replace("\\", "\\\\")
        lines.extend([
            f"  {name}:",
            f'    path: "{escaped}"',
            "    read: true",
            "    write: true",
            "    execute: true",
        ])
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def roots(tmp_path, monkeypatch):
    pla = tmp_path / "pla"
    workspace = pla / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(local_tools, "WORKSPACE", workspace.resolve())
    monkeypatch.setattr(local_tools, "PLA_ROOT", pla.resolve())
    monkeypatch.delenv(local_tools.WORKSPACES_CONFIG_ENV, raising=False)
    return workspace, pla


def test_default_root_remains_workspace(roots):
    workspace, pla = roots
    (workspace / "same.txt").write_text("workspace", encoding="utf-8")
    (pla / "same.txt").write_text("pla", encoding="utf-8")
    assert "workspace" in local_tools.read_text("same.txt")["content"]


def test_machine_local_roots_are_loaded_dynamically(roots, tmp_path):
    workspace, pla = roots
    desktop = tmp_path / "Desktop"
    rerun_thesis = tmp_path / "rerun_thesis"
    desktop.mkdir()
    rerun_thesis.mkdir()
    _write_workspace_config(pla, {"desktop": desktop})

    (workspace / "same.txt").write_text("workspace", encoding="utf-8")
    (desktop / "same.txt").write_text("desktop", encoding="utf-8")
    (rerun_thesis / "same.txt").write_text("rerun", encoding="utf-8")
    assert "desktop" in local_tools.read_text("same.txt", root="desktop")["content"]

    # Re-read on every call: adding a local root does not require a source edit or restart.
    _write_workspace_config(
        pla,
        {"desktop": desktop, "rerun_thesis": rerun_thesis},
    )
    assert "rerun" in local_tools.read_text(
        "same.txt", root="rerun_thesis"
    )["content"]
    assert local_tools.available_roots()["desktop"] == {
        "read": True, "write": True, "execute": True,
    }
    assert local_tools.available_roots()["rerun_thesis"] == {
        "read": True, "write": True, "execute": True,
    }


def test_configured_root_cannot_overlap_pla_source_tree(roots, tmp_path):
    _, pla = roots
    _write_workspace_config(pla, {"dangerous": pla.parent})
    with pytest.raises(ValueError, match="must not overlap the PLA source tree"):
        local_tools.available_roots()


def test_workspace_registry_mutations_use_config_sha_cas(roots, tmp_path):
    _, pla = roots
    external = tmp_path / "external"
    external.mkdir()

    initial = local_tools.workspace_roots_get()
    assert initial["config_exists"] is False
    assert initial["config_sha256"] is None

    created = local_tools.workspace_root_upsert(
        "external",
        str(external),
        True,
        True,
        False,
        initial["config_sha256"],
    )
    assert created["updated_root"] == "external"
    assert created["roots"]["external"]["write"] is True
    assert "external" in local_tools.available_roots()

    with pytest.raises(ValueError, match="changed since inspection"):
        local_tools.workspace_root_upsert(
            "stale",
            str(tmp_path / "stale"),
            True,
            False,
            False,
            initial["config_sha256"],
        )

    removed = local_tools.workspace_root_remove(
        "external",
        created["config_sha256"],
    )
    assert removed["removed_root"] == "external"
    assert "external" not in local_tools.available_roots()


def test_explicit_pla_root_is_unambiguous_when_it_contains_workspace(roots):
    workspace, pla = roots
    (workspace / "nested.txt").write_text("nested", encoding="utf-8")
    assert local_tools.safe_path("workspace/nested.txt", "pla") == workspace / "nested.txt"
    assert local_tools.safe_path("nested.txt", "workspace") == workspace / "nested.txt"


def test_unknown_root_is_rejected(roots):
    with pytest.raises(ValueError, match="Unknown root"):
        local_tools.read_text("x", root="unknown")


@pytest.mark.parametrize("path", ["../outside", "absolute"])
def test_pla_escape_and_external_absolute_path_are_rejected(roots, path):
    _, pla = roots
    candidate = str(pla.parent / "outside") if path == "absolute" else path
    with pytest.raises(ValueError, match="outside root 'pla'"):
        local_tools.safe_path(candidate, "pla")


def test_unc_is_rejected_for_pla(roots):
    with pytest.raises(ValueError, match="UNC"):
        local_tools.safe_path(r"\\server\share", "pla")


def test_drive_relative_path_is_rejected_for_pla(roots):
    with pytest.raises(ValueError, match="outside root 'pla'"):
        local_tools.safe_path("C:relative.txt", "pla")


def test_symlink_or_junction_escape_is_rejected(roots):
    _, pla = roots
    outside = pla.parent / "outside"
    outside.mkdir()
    (outside / "data.txt").write_text("outside", encoding="utf-8")
    link = pla / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
    with pytest.raises(ValueError, match="outside root 'pla'"):
        local_tools.read_text("link/data.txt", root="pla")


def test_real_pla_root_supports_self_read_list_and_search():
    listing = local_tools.list_directory(root="pla")
    read = local_tools.read_text("server.py", root="pla")
    search = local_tools.search_text(
        "execute_local_tool", root="pla", path=".", glob="*.py",
    )
    assert "server.py" in listing
    assert read["path"] == "server.py" and "FastMCP" in read["content"]
    assert search["match_count"] >= 1


def test_pla_write_replace_and_expected_hash(roots):
    _, pla = roots
    created = local_tools.write_text("source.py", "old\n", root="pla")
    replaced = local_tools.replace_text(
        "source.py", "old", "new", expected_sha256=created["sha256"], root="pla",
    )
    assert replaced["replacements"] == 1
    assert (pla / "source.py").read_text(encoding="utf-8") == "new\n"
    with pytest.raises(local_tools.FileChangedSinceRead):
        local_tools.write_text("source.py", "bad", expected_sha256="0" * 64, root="pla")


def test_pla_apply_patch(roots):
    _, pla = roots
    (pla / "source.py").write_bytes(b"old\n")
    digest = hashlib.sha256(b"old\n").hexdigest()
    result = local_tools.apply_patch(
        "source.py", "@@ -1 +1 @@\n-old\n+new\n", digest, root="pla",
    )
    assert result["status"] == "completed"
    assert (pla / "source.py").read_bytes() == b"new\n"


def test_pla_apply_changeset(roots):
    _, pla = roots
    for name in ("a.py", "b.py"):
        (pla / name).write_bytes(b"old\n")
    changes = [{
        "path": name,
        "expected_sha256": hashlib.sha256(b"old\n").hexdigest(),
        "patch": "@@ -1 +1 @@\n-old\n+new\n",
    } for name in ("a.py", "b.py")]
    result = local_tools.apply_changeset(changes, root="pla")
    assert result["status"] == "completed" and result["files_applied"] == 2
    assert (pla / "a.py").read_bytes() == (pla / "b.py").read_bytes() == b"new\n"


@pytest.mark.parametrize("path", [
    ".git/config", "state/tasks.sqlite3", "cache/item", "tmp/item",
    "__pycache__/source.pyc", "auth/credential.json", "auth/token.secret",
    "runtime.sqlite3", "config/workspaces.local.yaml",
    "config/windows_actions.local.json",
])
def test_pla_protected_paths_cannot_be_written(roots, path):
    _, pla = roots
    with pytest.raises(ValueError, match="Protected path"):
        local_tools.write_text(path, "blocked", root="pla")
    assert not (pla / path).exists()


@pytest.mark.parametrize("path", [
    ".git/config", "state/tasks.sqlite3", "auth/credential.json",
    "config/workspaces.local.yaml", "config/windows_actions.local.json",
])
def test_pla_private_paths_cannot_be_read(roots, path):
    _, pla = roots
    target = pla / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("private", encoding="utf-8")
    with pytest.raises(ValueError, match="Protected path"):
        local_tools.read_text(path, root="pla")


def test_protected_policy_is_shared_by_list_search_patch_and_changeset(roots):
    _, pla = roots
    git_file = pla / ".git" / "config"
    git_file.parent.mkdir()
    git_file.write_bytes(b"old\n")
    with pytest.raises(ValueError, match="Protected path"):
        local_tools.list_directory(".git", root="pla")
    with pytest.raises(ValueError, match="Protected path"):
        local_tools.search_text("old", path=".git", root="pla")
    with pytest.raises(ValueError, match="Protected path"):
        local_tools.apply_patch(
            ".git/config", "@@ -1 +1 @@\n-old\n+new\n", root="pla",
        )
    result = local_tools.apply_changeset([{
        "path": ".git/config",
        "expected_sha256": hashlib.sha256(b"old\n").hexdigest(),
        "patch": "@@ -1 +1 @@\n-old\n+new\n",
    }], root="pla")
    assert result["status"] == "error" and result["files_applied"] == 0
    assert git_file.read_bytes() == b"old\n"


def test_tunnel_config_is_read_only_in_pla_root(roots):
    _, pla = roots
    target = pla / "config" / "tunnel.yaml"
    target.parent.mkdir()
    target.write_text("diagnostic: true\n", encoding="utf-8")
    assert "diagnostic" in local_tools.read_text("config/tunnel.yaml", root="pla")["content"]
    with pytest.raises(ValueError, match="read-only"):
        local_tools.write_text("config/tunnel.yaml", "changed", root="pla")
    assert target.read_text(encoding="utf-8") == "diagnostic: true\n"


def test_process_uses_pla_workdir_and_rejects_escape(roots):
    _, pla = roots
    (pla / "src").mkdir()
    result = local_tools.run_process(
        "python", ["-c", "import os;print(os.getcwd())"],
        root="pla", workdir="src",
    )
    assert result["returncode"] == 0 and result["cwd"] == "src"
    assert str(pla / "src").casefold() in result["stdout"].strip().casefold()
    with pytest.raises(ValueError, match="outside root 'pla'"):
        local_tools.run_process("python", root="pla", workdir="../outside")
    (pla / "state").mkdir()
    with pytest.raises(ValueError, match="Protected path"):
        local_tools.run_process("python", root="pla", workdir="state")


def test_git_is_not_newly_enabled_for_pla_root(roots):
    with pytest.raises(ValueError, match="Git execution"):
        local_tools.run_process("git", ["status"], root="pla")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell policy")
def test_powershell_literal_path_uses_pla_root(roots):
    _, pla = roots
    (pla / "source.py").write_text("hello", encoding="utf-8")
    result = local_tools.run_powershell(
        "Get-Content", {"LiteralPath": "source.py", "Raw": True}, root="pla",
    )
    assert result["returncode"] == 0 and "hello" in result["stdout"]
    escaped = execute_local_tool("run_powershell", {
        "command": "Get-Content", "parameters": {"LiteralPath": "../outside"},
        "root": "pla",
    })
    assert not escaped.ok and "outside root 'pla'" in escaped.error["message"]


def test_execute_actions_forwards_root_to_shared_policy(roots):
    _, pla = roots
    result = execute_actions_request([
        {"tool": "write_text", "arguments": {"root": "pla", "path": "action.txt", "content": "ok"}},
        {"tool": "read_text", "arguments": {"root": "pla", "path": "action.txt"}},
    ])
    assert result["status"] == "completed"
    assert (pla / "action.txt").read_text(encoding="utf-8") == "ok"


def test_task_worker_forwards_root_to_shared_policy(roots, tmp_path):
    _, pla = roots
    (pla / "worker.txt").write_text("worker", encoding="utf-8")
    store = TaskStore(db_path=tmp_path / "worker-state.sqlite3")
    try:
        task_id = store.submit({
            "tool": "read_text", "arguments": {"root": "pla", "path": "worker.txt"},
        })["task_id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            record = store.get(task_id)
            if record["status"] in TERMINAL:
                break
            time.sleep(0.01)
        assert record["status"] == "completed"
        assert "worker" in record["result"]["result"]["content"]
    finally:
        store.close()


def test_available_roots_exposes_capabilities_without_paths(roots):
    assert local_tools.available_roots() == {
        "workspace": {"read": True, "write": True, "execute": True},
        "pla": {"read": True, "write": True, "execute": True},
    }


def test_internal_tool_root_schema_is_dynamic_not_hardcoded(roots):
    root_schemas = []
    for tool in INTERNAL_TOOL_SCHEMAS:
        properties = tool["input_schema"].get("properties", {})
        if "root" in properties:
            root_schemas.append(properties["root"])
    assert root_schemas
    assert all(schema["type"] == "string" for schema in root_schemas)
    assert all("enum" not in schema for schema in root_schemas)
