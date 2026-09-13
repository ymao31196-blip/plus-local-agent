import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from providers import computer_winapp_server as computer


def _prepare_backend(tmp_path, monkeypatch, *, version="0.5.0"):
    package_dir = (
        tmp_path
        / ".provider_envs"
        / "computer"
        / "node_modules"
        / "@microsoft"
        / "winappcli"
    )
    script = package_dir / "dist" / "winapp.js"
    script.parent.mkdir(parents=True)
    script.write_text("// fixture", encoding="utf-8")
    (package_dir / "package.json").write_text(
        json.dumps({"version": version, "bin": {"winapp": "dist/winapp.js"}}),
        encoding="utf-8",
    )
    node = tmp_path / "node.exe"
    node.write_bytes(b"node")
    monkeypatch.setattr(computer, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(computer, "PACKAGE_DIR", package_dir)
    monkeypatch.setattr(computer, "STATE_DIR", tmp_path / "state" / "computer")
    monkeypatch.setattr(
        computer,
        "CACHE_DIR",
        tmp_path / "state" / "computer" / "winapp-cache",
    )
    monkeypatch.setattr(
        computer.shutil,
        "which",
        lambda name: str(node) if name in {"node.exe", "node"} else None,
    )
    return node, script


def test_backend_requires_reviewed_provider_environment(tmp_path, monkeypatch):
    node = tmp_path / "node.exe"
    node.write_bytes(b"node")
    monkeypatch.setattr(computer, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        computer,
        "PACKAGE_DIR",
        tmp_path / ".provider_envs" / "computer" / "missing",
    )
    monkeypatch.setattr(
        computer.shutil,
        "which",
        lambda name: str(node) if name in {"node.exe", "node"} else None,
    )

    with pytest.raises(RuntimeError, match="setup_providers.ps1"):
        computer._resolve_winapp_launch()


def test_backend_uses_pinned_provider_scoped_winapp(tmp_path, monkeypatch):
    node, script = _prepare_backend(tmp_path, monkeypatch)

    executable, resolved_script, version = computer._resolve_winapp_launch()

    assert executable == str(node.resolve())
    assert resolved_script == str(script.resolve())
    assert version == "0.5.0"


def test_backend_rejects_version_drift(tmp_path, monkeypatch):
    _prepare_backend(tmp_path, monkeypatch, version="0.5.1")

    with pytest.raises(RuntimeError, match="version drift"):
        computer._resolve_winapp_launch()


def test_target_requires_exactly_one_app_or_hwnd():
    assert computer._target_args("notepad", None) == ["-a", "notepad"]
    assert computer._target_args(None, 123) == ["-w", "123"]

    with pytest.raises(ValueError, match="either app or hwnd"):
        computer._target_args("notepad", 123)
    with pytest.raises(ValueError, match="required"):
        computer._target_args(None, None)


def test_run_ui_is_shell_free_json_and_disables_update_check(tmp_path, monkeypatch):
    node, script = _prepare_backend(tmp_path, monkeypatch)
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout='{"matchCount": 1}',
            stderr="",
        )

    monkeypatch.setattr(computer.subprocess, "run", fake_run)

    result = computer._run_ui(["search", "Save", "-a", "demo"])

    assert result["status"] == "completed"
    assert result["backend_version"] == "0.5.0"
    assert result["result"]["matchCount"] == 1
    assert captured["argv"] == [
        str(node.resolve()),
        str(script.resolve()),
        "ui",
        "search",
        "Save",
        "-a",
        "demo",
        "--json",
    ]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["env"]["WINAPP_CLI_UPDATE_CHECK"] == "0"
    assert captured["kwargs"]["env"]["WINAPP_CLI_CACHE_DIRECTORY"].endswith(
        "state\\computer\\winapp-cache"
    ) or captured["kwargs"]["env"]["WINAPP_CLI_CACHE_DIRECTORY"].endswith(
        "state/computer/winapp-cache"
    )


def test_run_ui_preserves_parseable_no_match_envelope(tmp_path, monkeypatch):
    _prepare_backend(tmp_path, monkeypatch)

    monkeypatch.setattr(
        computer.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout='{"matchCount": 0}',
            stderr="",
        ),
    )

    result = computer._run_ui(["search", "missing", "-a", "demo"])

    assert result["status"] == "not_matched"
    assert result["returncode"] == 1
    assert result["result"] == {"matchCount": 0}


def test_managed_screenshot_path_must_stay_under_artifact_plane(tmp_path, monkeypatch):
    monkeypatch.setattr(computer, "PROJECT_ROOT", tmp_path)
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("AGENT_WORKSPACE", str(workspace))

    allowed = (
        workspace
        / ".capability_io"
        / "invocation"
        / "outputs"
        / "output_path"
        / "screenshot.png"
    ).resolve()
    assert computer._managed_png_path(str(allowed)) == allowed

    with pytest.raises(ValueError, match="absolute broker-managed"):
        computer._managed_png_path("workspace/.capability_io/screenshot.png")
    with pytest.raises(ValueError, match="Artifact Plane"):
        computer._managed_png_path(str((tmp_path / "outside.png").resolve()))
    with pytest.raises(ValueError, match="end in .png"):
        computer._managed_png_path(
            str(
                (
                    workspace
                    / ".capability_io"
                    / "invocation"
                    / "outputs"
                    / "output_path"
                    / "output.txt"
                ).resolve()
            )
        )


def test_v14_safe_keys_exclude_modifier_and_system_shortcuts():
    assert "enter" in computer._SAFE_KEYS
    assert "tab" in computer._SAFE_KEYS
    assert "ctrl+a" not in computer._SAFE_KEYS
    assert "alt+f4" not in computer._SAFE_KEYS
    assert "win+shift+v" not in computer._SAFE_KEYS


def test_type_text_uses_targeted_send_input_and_chunks(monkeypatch):
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        return {
            "status": "completed",
            "returncode": 0,
            "backend": "microsoft-winapp-cli",
            "backend_version": "0.5.0",
            "result": {},
        }

    monkeypatch.setattr(computer, "_run_ui", fake_run)
    monkeypatch.setattr(computer.time, "sleep", lambda _seconds: None)

    result = computer.type_text("Editor", "x" * 300, hwnd=42)

    assert len(calls) == 3
    assert all("--via" in call and "send-input" in call for call in calls)
    assert all("--allow-system-keys" not in call for call in calls)
    assert all("--target" in call and "Editor" in call for call in calls)
    assert all("-w" in call and "42" in call for call in calls)
    assert result["result"] == {
        "target": "Editor",
        "via": "send-input",
        "characters": 300,
        "chunk_count": 3,
    }


def test_type_text_rejects_empty_or_oversized_input():
    with pytest.raises(ValueError, match="non-empty"):
        computer.type_text("Editor", "", hwnd=42)
    with pytest.raises(ValueError, match="4096"):
        computer.type_text("Editor", "x" * 4097, hwnd=42)


def test_press_key_uses_send_input_without_system_key_opt_in(monkeypatch):
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        return {
            "status": "completed",
            "returncode": 0,
            "backend": "microsoft-winapp-cli",
            "backend_version": "0.5.0",
            "result": {},
        }

    monkeypatch.setattr(computer, "_run_ui", fake_run)

    computer.press_key("enter", hwnd=42, selector="Editor")

    assert calls == [[
        "send-keys", "enter", "--via", "send-input",
        "--target", "Editor", "-w", "42",
    ]]
    assert "--allow-system-keys" not in calls[0]
