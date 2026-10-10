"""RC.8 Desktop WPS npm runtime hardening.

These tests mock process execution. They specifically protect installed/frozen
Desktop from accidentally launching a user's npm.cmd through PATH.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from desktop_runtime.installer import ComponentInstaller
from provider import source_provider_setup as source


def _source_spec(tmp_path):
    spec = tmp_path / "wps-office.source.json"
    spec.write_text(json.dumps({
        "schema_version": 1,
        "kind": "git_npm",
        "repository_url": "https://github.com/lc2panda/wps-skills.git",
        "revision": "a" * 40,
        "package_subdir": "wps-office-mcp",
        "entrypoint": "dist/index.js",
        "node_min_major": 18,
        "patches": [],
    }), encoding="utf-8")
    return spec


def test_source_managed_npm_uses_bundled_node_not_host_cmd(tmp_path, monkeypatch):
    spec = _source_spec(tmp_path)
    root = tmp_path / "private"
    source_root = root / ".provider_sources/wps-office"
    (source_root / ".git").mkdir(parents=True)
    package_root = source_root / "wps-office-mcp"
    (package_root / "dist").mkdir(parents=True)
    (package_root / "dist/index.js").write_text("module.exports={};\n")
    (package_root / "package.json").write_text("{}\n")
    (package_root / "package-lock.json").write_text("{}\n")
    node = tmp_path / "bundled/node.exe"
    npm_cli = tmp_path / "bundled/node-runtime/node_modules/npm/bin/npm-cli.js"
    node.parent.mkdir(parents=True)
    npm_cli.parent.mkdir(parents=True)
    node.write_bytes(b"fake-node")
    npm_cli.write_text("require('../lib/cli.js')(process);\n")
    git = str(tmp_path / "git.exe")
    monkeypatch.setattr(source.shutil, "which",
                        lambda name: git if name in ("git.exe", "git") else (
                            "C:/untrusted/npm.cmd" if name == "npm.cmd" else None))
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[-3:] == ["remote", "get-url", "origin"]:
            return SimpleNamespace(returncode=0, stdout="https://github.com/lc2panda/wps-skills.git\n", stderr="")
        if argv[-2:] == ["rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="a" * 40 + "\n", stderr="")
        if argv == [str(node.resolve()), "--version"]:
            return SimpleNamespace(returncode=0, stdout="v24.19.0\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(source.subprocess, "run", fake_run)
    result = source.setup_git_npm_source(
        root, "wps-office", spec, timeout_seconds=30,
        managed_node=node, managed_npm_cli=npm_cli)
    assert result["revision"] == "a" * 40
    npm_invocations = [argv for argv, _ in calls if "ci" in argv or "build" in argv]
    assert npm_invocations == [
        [str(node.resolve()), str(npm_cli.resolve()), "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
        [str(node.resolve()), str(npm_cli.resolve()), "run", "build", "--ignore-scripts"],
    ]
    assert all(k["shell"] is False for _, k in calls)
    assert all("npm.cmd" not in " ".join(argv) for argv, _ in calls)


def test_managed_node_pair_validation(tmp_path, monkeypatch):
    spec = _source_spec(tmp_path)
    monkeypatch.setattr(source.shutil, "which", lambda name: "C:/Git/git.exe")
    node = tmp_path / "node.exe"
    node.write_bytes(b"node")
    with pytest.raises(ValueError, match="supplied together"):
        source.setup_git_npm_source(tmp_path, "wps-office", spec, timeout_seconds=30,
                                    managed_node=node)
    with pytest.raises(RuntimeError, match="Bundled Node or npm CLI is missing"):
        source.setup_git_npm_source(tmp_path, "wps-office", spec, timeout_seconds=30,
                                    managed_node=node, managed_npm_cli=tmp_path / "missing.js")


def test_desktop_source_installer_passes_own_managed_binaries(tmp_path, monkeypatch):
    resources = tmp_path / "resources"
    spec_dir = resources / "provider-assets/provider_specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "wps-office.source.json").write_bytes(_source_spec(tmp_path).read_bytes())
    node = resources / "node.exe"
    node.write_bytes(b"node")
    npm_cli = resources / "node-runtime/node_modules/npm/bin/npm-cli.js"
    npm_cli.parent.mkdir(parents=True)
    npm_cli.write_text("// controlled cli\n")
    installer = ComponentInstaller(tmp_path / "private", resources)
    seen = []
    def fake_source(root, provider, spec, **kwargs):
        seen.append((root, provider, spec, kwargs))
        return {"status": "installed"}
    monkeypatch.setattr(source, "setup_git_npm_source", fake_source)
    receipt = installer.install("wps-office")
    assert receipt["status"] == "installed"
    assert len(seen) == 1
    assert seen[0][1] == "wps-office"
    assert seen[0][3]["managed_node"] == node
    assert seen[0][3]["managed_npm_cli"] == npm_cli
    assert not receipt["activated"]


def test_desktop_source_installer_rejects_missing_bundled_cli(tmp_path, monkeypatch):
    resources = tmp_path / "resources"
    spec_dir = resources / "provider-assets/provider_specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "wps-office.source.json").write_bytes(_source_spec(tmp_path).read_bytes())
    (resources / "node.exe").write_bytes(b"node")
    installer = ComponentInstaller(tmp_path / "private", resources)
    with pytest.raises(ValueError, match="Bundled Node/npm CLI is missing"):
        installer.install("wps-office")
    assert not (installer.project.root / "receipts/wps-office.json").exists()
