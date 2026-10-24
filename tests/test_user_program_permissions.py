from __future__ import annotations

import json
from pathlib import Path

import pytest

from execution.execution_program_policy import require_allowed_program
from execution.user_program_permissions import (
    change_permission,
    executable_is_granted,
    snapshot,
)


@pytest.fixture
def permission_file(monkeypatch, tmp_path):
    path = tmp_path / "private" / "program_permissions.json"
    monkeypatch.setenv("PLA_PROGRAM_PERMISSIONS_FILE", str(path))
    return path


def test_missing_config_does_not_grant_random_program(permission_file, tmp_path):
    executable = tmp_path / "my_tool.exe"
    executable.write_bytes(b"example")
    assert snapshot()["programs"] == []
    assert not executable_is_granted(str(executable))
    with pytest.raises(ValueError, match="Program not allowed"):
        require_allowed_program(str(executable))


def test_explicit_path_grant_disable_regrant_revoke(permission_file, tmp_path):
    executable = tmp_path / "my_tool.exe"
    executable.write_bytes(b"example")
    initial = snapshot()
    granted = change_permission("grant", executable=str(executable), expected_sha256=initial["config_sha256"])
    assert granted["programs"] == [{"path": str(executable.resolve()), "enabled": True}]
    assert require_allowed_program(str(executable)) == "my_tool.exe"
    # Exact path, not the basename, is the authority.
    with pytest.raises(ValueError, match="Program not allowed"):
        require_allowed_program("my_tool.exe")
    off = change_permission("disable", executable=str(executable), expected_sha256=granted["config_sha256"])
    assert not executable_is_granted(str(executable))
    on = change_permission("grant", executable=str(executable), expected_sha256=off["config_sha256"])
    assert executable_is_granted(str(executable))
    done = change_permission("revoke", executable=str(executable), expected_sha256=on["config_sha256"])
    assert done["programs"] == []
    assert not executable_is_granted(str(executable))


def test_optimistic_concurrency_rejects_stale_revoke(permission_file, tmp_path):
    executable = tmp_path / "video.exe"
    executable.write_bytes(b"example")
    granted = change_permission("grant", executable=str(executable), expected_sha256=None)
    with pytest.raises(ValueError, match="changed"):
        change_permission("revoke", executable=str(executable), expected_sha256=None)
    assert snapshot()["config_sha256"] == granted["config_sha256"]


def test_cannot_authorize_relative_path_or_directory(permission_file, tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        change_permission("grant", executable="ffmpeg.exe", expected_sha256=None)
    with pytest.raises(ValueError, match="absolute"):
        change_permission("grant", executable=str(tmp_path), expected_sha256=None)


def test_corrupt_policy_fails_closed(permission_file, tmp_path):
    executable = tmp_path / "video.exe"
    executable.write_bytes(b"example")
    permission_file.parent.mkdir(parents=True)
    permission_file.write_text("{broken", encoding="utf-8")
    assert not executable_is_granted(str(executable))
    with pytest.raises(json.JSONDecodeError):
        snapshot()




def test_permission_policy_is_protected_from_ordinary_pla_file_tools():
    from host.workspace_manager import build_root_policy
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        policy = build_root_policy(root / "workspace", root)
        for access in ("read", "write", "execute"):
            with pytest.raises(ValueError, match="Protected path"):
                policy.resolve("pla", "config/program_permissions.local.json", access)


def test_shell_host_cannot_be_added_in_v1(permission_file, tmp_path):
    shell = tmp_path / "powershell.exe"
    shell.write_bytes(b"example")
    with pytest.raises(ValueError, match="Shell interpreters"):
        change_permission("grant", executable=str(shell), expected_sha256=None)


def test_private_desktop_command_is_the_mutation_boundary(tmp_path):
    from types import SimpleNamespace
    from desktop_runtime.manager import Manager
    manager = Manager.__new__(Manager)
    manager.config = SimpleNamespace(root=tmp_path)
    manager._record = lambda message: None
    executable = tmp_path / "video.exe"
    executable.write_bytes(b"example")

    before = manager.dispatch("program_permissions", {"operation": "list"})
    assert before["programs"] == []
    granted = manager.dispatch("program_permissions", {
        "operation": "grant", "path": str(executable), "expected_sha256": None
    })
    assert granted["programs"][0]["enabled"] is True
    with pytest.raises(ValueError, match="changed"):
        manager.dispatch("program_permissions", {
            "operation": "disable", "path": str(executable), "expected_sha256": None
        })
    disabled = manager.dispatch("program_permissions", {
        "operation": "disable", "path": str(executable),
        "expected_sha256": granted["config_sha256"]
    })
    assert disabled["programs"][0]["enabled"] is False
    with pytest.raises(ValueError, match="Unexpected"):
        manager.dispatch("program_permissions", {"operation": "list", "path": "ignored"})


def test_full_program_path_runs_under_existing_workspace_policy(permission_file, tmp_path, monkeypatch):
    import os
    from tooling import local_tools
    system_root = os.environ.get("SystemRoot")
    if os.name != "nt" or not system_root:
        pytest.skip("Windows only executable integration")
    program = Path(system_root) / "System32" / "whoami.exe"
    if not program.is_file():
        pytest.skip("whoami.exe unavailable")
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    with pytest.raises(ValueError, match="Program not allowed"):
        local_tools.run_process(str(program), [], backend="in_process")
    change_permission("grant", executable=str(program), expected_sha256=None)
    result = local_tools.run_process(str(program), [], backend="in_process")
    assert result["returncode"] == 0
    assert result["stdout"].strip()

def test_user_grant_does_not_allow_arbitrary_session_or_powershell(permission_file, tmp_path):
    executable = tmp_path / "video.exe"
    executable.write_bytes(b"example")
    change_permission("grant", executable=str(executable), expected_sha256=None)
    with pytest.raises(ValueError, match="Program not allowed"):
        require_allowed_program(str(executable), session=True)
    with pytest.raises(ValueError, match="Program not allowed"):
        require_allowed_program("powershell.exe")
