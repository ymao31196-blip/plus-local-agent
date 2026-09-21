from pathlib import Path

import pytest

import browser_runtime as runtime


def test_browser_runtime_requires_reviewed_provider_environment(tmp_path, monkeypatch):
    node = tmp_path / "node.exe"
    node.write_bytes(b"node")
    monkeypatch.setattr(runtime, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        runtime.shutil,
        "which",
        lambda name: str(node) if name in {"node.exe", "node"} else None,
    )

    with pytest.raises(RuntimeError, match="setup_providers.ps1"):
        runtime._resolve_playwright_launch()


def test_browser_runtime_uses_only_provider_scoped_playwright_cli(tmp_path, monkeypatch):
    node = tmp_path / "node.exe"
    node.write_bytes(b"node")
    cli = (
        tmp_path
        / ".provider_envs"
        / "browser"
        / "node_modules"
        / "@playwright"
        / "mcp"
        / "cli.js"
    )
    cli.parent.mkdir(parents=True)
    cli.write_text("// reviewed test fixture", encoding="utf-8")
    (cli.parent / "package.json").write_text(
        '{"version":"0.0.82"}',
        encoding="utf-8",
    )

    monkeypatch.setattr(runtime, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        runtime.shutil,
        "which",
        lambda name: str(node) if name in {"node.exe", "node"} else None,
    )

    executable, prefix_args, source = runtime._resolve_playwright_launch()

    assert executable == str(node.resolve())
    assert prefix_args == [str(cli)]
    assert source == "provider_env"


def test_browser_runtime_rejects_provider_version_drift(tmp_path, monkeypatch):
    node = tmp_path / "node.exe"
    node.write_bytes(b"node")
    cli = (
        tmp_path
        / ".provider_envs"
        / "browser"
        / "node_modules"
        / "@playwright"
        / "mcp"
        / "cli.js"
    )
    cli.parent.mkdir(parents=True)
    cli.write_text("// reviewed test fixture", encoding="utf-8")
    (cli.parent / "package.json").write_text(
        '{"version":"0.0.79"}',
        encoding="utf-8",
    )

    monkeypatch.setattr(runtime, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        runtime.shutil,
        "which",
        lambda name: str(node) if name in {"node.exe", "node"} else None,
    )

    with pytest.raises(RuntimeError, match="version drift"):
        runtime._resolve_playwright_launch()


def test_browser_runtime_diagnostics_redact_common_secret_fields():
    raw = (
        "Authorization:BearerSecret "
        "Cookie=session123 "
        "password=hunter2 "
        "token=abc123 "
        "secret:xyz"
    )

    redacted = runtime._redact_diagnostic_text(raw)

    assert "BearerSecret" not in redacted
    assert "session123" not in redacted
    assert "hunter2" not in redacted
    assert "abc123" not in redacted
    assert "xyz" not in redacted
    assert redacted.count("<REDACTED>") == 5
