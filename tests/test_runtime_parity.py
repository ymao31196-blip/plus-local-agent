import local_tools
import runtime_parity
from runtime_parity import locate_codex, run_pla_probe_suite


def test_runtime_parity_pla_suite_passes(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(local_tools, "WORKSPACE", workspace.resolve())

    results = run_pla_probe_suite(root="workspace")

    assert [item.name for item in results] == [
        "pla.oneshot.stdout_stderr",
        "pla.oneshot.timeout",
        "pla.session.pipe",
        "pla.session.conpty",
    ]
    assert all(item.status == "pass" for item in results)
    conpty = next(item for item in results if item.name == "pla.session.conpty")
    assert conpty.details["resized"] is True
    assert conpty.details["permissions"]["route"] == "interactive_conpty_session"


def test_locate_codex_uses_packaged_fallback(tmp_path, monkeypatch):
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"placeholder")
    monkeypatch.delenv("CODEX_EXECUTABLE", raising=False)
    monkeypatch.setattr(runtime_parity.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runtime_parity,
        "_locate_windows_appx_codex",
        lambda: str(executable),
    )

    assert locate_codex() == str(executable)
