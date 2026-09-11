import json
from pathlib import Path

import pytest

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
    assert manifest.python_path == (
        tmp_path / ".provider_envs/demo/Scripts/python.exe"
    ).resolve()
    assert manifest.args == ("-m", "demo_server")
    assert manifest.cwd == tmp_path.resolve()
    assert manifest.tool_allowlist == ("convert",)
    assert manifest.tool_overrides["convert"]["risk_level"] == "read"


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
