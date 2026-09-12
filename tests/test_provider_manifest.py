import json
from pathlib import Path

import pytest

import provider_manifest as manifest_module
from provider_manifest import load_provider_manifest, load_provider_manifests


def write_manifest(root: Path, provider_id: str = "demo", **updates):
    manifest_dir = root / "provider_manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "id": provider_id,
        "autostart": True,
        "mode": "legacy",
        "runtime": {
            "kind": "isolated_python_stdio",
            "python": f".provider_envs/{provider_id}/Scripts/python.exe",
            "args": ["-m", "demo_server"],
            "cwd": ".",
        },
        "tool_allowlist": ["convert"],
        "tool_overrides": {
            "convert": {
                "risk_level": "read",
                "requires_confirmation": False,
            }
        },
    }
    payload.update(updates)
    path = manifest_dir / f"{provider_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_provider_manifest_resolves_isolated_runtime(tmp_path):
    path = write_manifest(tmp_path)

    manifest = load_provider_manifest(path, tmp_path)

    assert manifest.provider_id == "demo"
    assert manifest.autostart is True
    assert manifest.mode == "legacy"
    assert manifest.runtime_kind == "isolated_python_stdio"
    assert manifest.command_path == (
        tmp_path / ".provider_envs/demo/Scripts/python.exe"
    ).resolve()
    assert manifest.python_path == (
        tmp_path / ".provider_envs/demo/Scripts/python.exe"
    ).resolve()
    assert manifest.args == ("-m", "demo_server")
    assert manifest.cwd == tmp_path.resolve()
    assert manifest.tool_allowlist == ("convert",)
    assert manifest.tool_overrides["convert"]["risk_level"] == "read"


def test_load_provider_manifest_resolves_executable_runtime(tmp_path):
    executable = tmp_path / "bin" / "demo-mcp.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fake-executable")
    path = write_manifest(
        tmp_path,
        runtime={
            "kind": "executable_stdio",
            "command": "bin/demo-mcp.exe",
            "args": ["--stdio"],
            "cwd": ".",
        },
    )

    manifest = load_provider_manifest(path, tmp_path)

    assert manifest.runtime_kind == "executable_stdio"
    assert manifest.command_path == executable.resolve()
    assert manifest.python_path is None
    assert manifest.args == ("--stdio",)


def test_executable_runtime_allows_absolute_command(tmp_path):
    executable = tmp_path / "external-mcp.exe"
    executable.write_bytes(b"fake-executable")
    path = write_manifest(
        tmp_path,
        runtime={
            "kind": "executable_stdio",
            "command": str(executable.resolve()),
            "args": [],
            "cwd": ".",
        },
    )

    manifest = load_provider_manifest(path, tmp_path)

    assert manifest.command_path == executable.resolve()


def test_executable_runtime_resolves_bare_command_from_path(tmp_path, monkeypatch):
    executable = tmp_path / "winget-mcp.exe"
    executable.write_bytes(b"fake-executable")
    monkeypatch.setattr(
        manifest_module.shutil,
        "which",
        lambda name: str(executable) if name == "winget-mcp.exe" else None,
    )
    path = write_manifest(
        tmp_path,
        runtime={
            "kind": "executable_stdio",
            "command": "winget-mcp.exe",
            "args": [],
            "cwd": ".",
        },
    )

    manifest = load_provider_manifest(path, tmp_path)

    assert manifest.command_path == executable.resolve()


def test_executable_runtime_rejects_missing_bare_command(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest_module.shutil, "which", lambda _name: None)
    path = write_manifest(
        tmp_path,
        runtime={
            "kind": "executable_stdio",
            "command": "missing-mcp.exe",
            "args": [],
            "cwd": ".",
        },
    )

    with pytest.raises(ValueError, match="not found on PATH"):
        load_provider_manifest(path, tmp_path)


def test_executable_runtime_rejects_python_field(tmp_path):
    path = write_manifest(
        tmp_path,
        runtime={
            "kind": "executable_stdio",
            "command": "bin/demo.exe",
            "python": ".provider_envs/demo/Scripts/python.exe",
            "args": [],
            "cwd": ".",
        },
    )

    with pytest.raises(ValueError, match="python is not valid"):
        load_provider_manifest(path, tmp_path)


def test_manifest_rejects_python_outside_provider_environment(tmp_path):
    path = write_manifest(
        tmp_path,
        runtime={
            "kind": "isolated_python_stdio",
            "python": ".provider_envs/other/Scripts/python.exe",
            "args": [],
            "cwd": ".",
        },
    )

    with pytest.raises(ValueError, match="must be inside"):
        load_provider_manifest(path, tmp_path)


def test_manifest_rejects_project_escape(tmp_path):
    path = write_manifest(
        tmp_path,
        runtime={
            "kind": "isolated_python_stdio",
            "python": "../python.exe",
            "args": [],
            "cwd": ".",
        },
    )

    with pytest.raises(ValueError, match="escapes"):
        load_provider_manifest(path, tmp_path)


def test_manifest_rejects_unknown_top_level_field(tmp_path):
    path = write_manifest(tmp_path, secret_token="not-allowed")

    with pytest.raises(ValueError, match="Unknown provider manifest fields"):
        load_provider_manifest(path, tmp_path)


def test_manifest_rejects_override_outside_allowlist(tmp_path):
    path = write_manifest(
        tmp_path,
        tool_allowlist=["convert"],
        tool_overrides={
            "convert": {},
            "hidden_tool": {},
        },
    )

    with pytest.raises(ValueError, match="outside tool_allowlist"):
        load_provider_manifest(path, tmp_path)


def test_load_provider_manifests_rejects_duplicate_ids(tmp_path):
    first = write_manifest(tmp_path, "demo")
    second = first.parent / "duplicate.json"
    second.write_text(first.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate provider id"):
        load_provider_manifests(tmp_path)


def test_missing_manifest_directory_is_empty(tmp_path):
    assert load_provider_manifests(tmp_path) == {}
