import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from provider import provider_setup_runtime as setup_runtime
def test_setup_provider_requires_reviewed_spec(tmp_path):
    (tmp_path / "provider_specs").mkdir()

    with pytest.raises(ValueError, match="No reviewed dependency spec"):
        setup_runtime.setup_provider_dependencies(tmp_path, "missing")


@pytest.mark.parametrize("provider_id", ["", "../bad", "Bad Name", "a/b"])
def test_setup_provider_rejects_invalid_provider_id(tmp_path, provider_id):
    with pytest.raises(ValueError, match="provider_id is invalid"):
        setup_runtime.setup_provider_dependencies(tmp_path, provider_id)


def test_setup_provider_uses_fixed_script_and_shell_free_process(tmp_path, monkeypatch):
    spec_dir = tmp_path / "provider_specs"
    spec_dir.mkdir()
    (spec_dir / "computer.txt").write_text("fastmcp==4.0.3\n", encoding="utf-8")
    (spec_dir / "computer.npm.txt").write_text(
        "@microsoft/winappcli@0.5.0\n",
        encoding="utf-8",
    )
    script = tmp_path / "setup_providers.ps1"
    script.write_text("Write-Host ready\n", encoding="utf-8")
    powershell = tmp_path / "powershell.exe"
    powershell.write_bytes(b"ps")

    captured = {}

    monkeypatch.setattr(
        setup_runtime.shutil,
        "which",
        lambda name: str(powershell) if name == "powershell.exe" else None,
    )

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="PLA PROVIDERS READY\n", stderr="")

    monkeypatch.setattr(setup_runtime.subprocess, "run", fake_run)

    result = setup_runtime.setup_provider_dependencies(tmp_path, "computer")

    assert result["status"] == "completed"
    assert result["provider_id"] == "computer"
    assert result["python_spec"] is True
    assert result["node_spec"] is True
    assert captured["argv"] == [
        str(powershell.resolve()),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script.resolve()),
        "-Provider",
        "computer",
    ]
    assert captured["kwargs"]["cwd"] == str(tmp_path.resolve())
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["timeout"] == 300


def test_setup_provider_surfaces_process_failure(tmp_path, monkeypatch):
    spec_dir = tmp_path / "provider_specs"
    spec_dir.mkdir()
    (spec_dir / "demo.txt").write_text("fastmcp==4.0.3\n", encoding="utf-8")
    script = tmp_path / "setup_providers.ps1"
    script.write_text("throw fail\n", encoding="utf-8")
    powershell = tmp_path / "powershell.exe"
    powershell.write_bytes(b"ps")

    monkeypatch.setattr(setup_runtime.shutil, "which", lambda name: str(powershell))
    monkeypatch.setattr(
        setup_runtime.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="provider failed",
        ),
    )

    with pytest.raises(RuntimeError, match="provider failed"):
        setup_runtime.setup_provider_dependencies(tmp_path, "demo")


def test_source_provider_requires_pinned_commit(tmp_path):
    spec_dir = tmp_path / "provider_specs"
    spec_dir.mkdir()
    (spec_dir / "wps-office.source.json").write_text(
        json.dumps({
            "schema_version": 1,
            "kind": "git_npm",
            "repository_url": "https://github.com/lc2panda/wps-skills.git",
            "revision": "main",
            "package_subdir": "wps-office-mcp",
            "entrypoint": "dist/index.js",
            "node_min_major": 18,
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="40-character Git commit SHA"):
        setup_runtime.setup_provider_dependencies(tmp_path, "wps-office")


def test_source_provider_uses_fixed_git_and_npm_commands(tmp_path, monkeypatch):
    spec_dir = tmp_path / "provider_specs"
    spec_dir.mkdir()
    revision = "a" * 40
    repository_url = "https://github.com/lc2panda/wps-skills.git"
    patch_relative = "provider_patches/wps-office/windows-com-compat.patch"
    patch_path = tmp_path / patch_relative
    patch_path.parent.mkdir(parents=True)
    patch_path.write_text("--- a/file\n+++ b/file\n", encoding="utf-8")
    (spec_dir / "wps-office.source.json").write_text(
        json.dumps({
            "schema_version": 1,
            "kind": "git_npm",
            "repository_url": repository_url,
            "revision": revision,
            "package_subdir": "wps-office-mcp",
            "entrypoint": "dist/index.js",
            "node_min_major": 18,
            "patches": [patch_relative],
        }),
        encoding="utf-8",
    )

    source_root = tmp_path / ".provider_sources" / "wps-office"
    (source_root / ".git").mkdir(parents=True)
    package_root = source_root / "wps-office-mcp"
    (package_root / "dist").mkdir(parents=True)
    (package_root / "package.json").write_text("{}\n", encoding="utf-8")
    (package_root / "package-lock.json").write_text("{}\n", encoding="utf-8")
    (package_root / "dist" / "index.js").write_text("// built\n", encoding="utf-8")

    executables = {
        "git.exe": str(tmp_path / "git.exe"),
        "node.exe": str(tmp_path / "node.exe"),
        "npm.cmd": str(tmp_path / "npm.cmd"),
    }
    monkeypatch.setattr(
        setup_runtime.source_provider_setup.shutil,
        "which",
        lambda name: executables.get(name),
    )

    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[0] == executables["git.exe"] and argv[-3:] == ["remote", "get-url", "origin"]:
            return SimpleNamespace(returncode=0, stdout=repository_url + "\n", stderr="")
        if argv[0] == executables["git.exe"] and argv[-2:] == ["rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout=revision + "\n", stderr="")
        if argv == [executables["node.exe"], "--version"]:
            return SimpleNamespace(returncode=0, stdout="v24.19.0\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        setup_runtime.source_provider_setup.subprocess,
        "run",
        fake_run,
    )

    result = setup_runtime.setup_provider_dependencies(tmp_path, "wps-office")

    assert result["source_spec"] is True
    assert result["source"]["revision"] == revision
    assert result["source"]["patches"] == [patch_relative]
    argvs = [argv for argv, _kwargs in calls]
    assert [
        executables["git.exe"],
        "-C",
        str(source_root.resolve()),
        "apply",
        "--check",
        str(patch_path.resolve()),
    ] in argvs
    assert [
        executables["git.exe"],
        "-C",
        str(source_root.resolve()),
        "apply",
        "--whitespace=nowarn",
        str(patch_path.resolve()),
    ] in argvs
    assert [
        executables["npm.cmd"],
        "ci",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
    ] in argvs
    assert [
        executables["npm.cmd"],
        "run",
        "build",
        "--ignore-scripts",
    ] in argvs
    assert all(kwargs["shell"] is False for _argv, kwargs in calls)


def test_setup_script_supports_provider_filter_and_single_recreate_pass():
    root = Path(__file__).resolve().parents[1]
    script = (root / "setup_providers.ps1").read_text(encoding="utf-8")

    assert "[string[]]$Provider" in script
    assert "No reviewed dependency spec for provider(s)" in script
    assert "$selectedProviders = @()" in script
    assert '"*.source.json"' in script
    assert '$env:PYTHONPATH = Join-Path $projectRoot "src"' in script
    assert '"-m", "provider.setup_source_provider"' in script
    assert 'setup_source_provider.py' not in script
    assert 'Installing source-backed Provider' in script
    assert '"--provider", $provider' in script
    assert script.count("Remove-Item -LiteralPath $envDir -Recurse -Force") == 1
    assert "function Invoke-NativeChecked" in script
    assert '$ErrorActionPreference = "Continue"' in script
    assert "& $FilePath @ArgumentList 2>&1" in script
    assert "& $providerPython -m pip install" not in script
    assert "& $providerPython -m pip check" not in script
    assert "& $npmCommand.Source install" not in script
    assert "& $npmCommand.Source ls" not in script


def test_node_provider_specs_use_real_line_breaks_and_declare_packages():
    root = Path(__file__).resolve().parents[1]
    spec_dir = root / "provider_specs"
    npm_specs = sorted(spec_dir.glob("*.npm.txt"))

    assert npm_specs
    for spec in npm_specs:
        text = spec.read_text(encoding="utf-8")
        assert "\\n" not in text
        assert "\\r" not in text

        packages = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert packages, f"No Node packages declared in {spec.name}"

