import sys
from pathlib import Path
import subprocess

import pytest

import execution_backend
import execution_runner_runtime as runner_runtime
import local_tools
from runner_transport import RunnerSpikeClient


def _redirect_runner_state(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "runner-state"
    monkeypatch.setattr(runner_runtime, "STATE_DIR", state_dir)
    monkeypatch.setattr(runner_runtime, "STATE_PATH", state_dir / "runtime.json")
    monkeypatch.setattr(runner_runtime, "AUTH_PATH", state_dir / "runner.auth")


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["git", "init", "--quiet"],
        cwd=path,
        capture_output=True,
        text=True,
        shell=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_default_prefers_production_runner_when_healthy(tmp_path, monkeypatch):
    _redirect_runner_state(tmp_path, monkeypatch)
    started = runner_runtime.start_execution_runner()
    try:
        result = local_tools.run_process(
            sys.executable,
            ["-c", "print('PRODUCTION_DEFAULT_OK')"],
            root="pla",
        )
        assert result["stdout"] == "PRODUCTION_DEFAULT_OK\n"
        assert result["backend_preference"] == "default"
        assert result["resolved_backend_preference"] == "production_runner"
        assert result["runner"]["runner_instance_id"] == started["runner"]["runner_instance_id"]
        assert result["runner"]["process_isolation"] == "separate_process"
        assert result["production"]["status"] == "runner"
        assert result["production"]["execution_request_id"] == result["execution_request_id"]
    finally:
        runner_runtime.stop_execution_runner()


def test_default_falls_back_before_submit_when_runner_is_stopped(tmp_path, monkeypatch):
    _redirect_runner_state(tmp_path, monkeypatch)
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    marker = tmp_path / "marker.txt"
    script = (
        "from pathlib import Path; "
        f"p=Path({str(marker)!r}); "
        "p.write_text((p.read_text() if p.exists() else '') + 'x', encoding='utf-8')"
    )
    result = local_tools.run_process(
        sys.executable,
        ["-c", script],
        root="workspace",
    )
    assert marker.read_text(encoding="utf-8") == "x"
    assert result["runner"]["process_isolation"] == "shared_http_process"
    assert result["production"]["status"] == "fallback_pre_submit"


def test_explicit_in_process_bypasses_healthy_runner(tmp_path, monkeypatch):
    _redirect_runner_state(tmp_path, monkeypatch)
    runner_runtime.start_execution_runner()
    try:
        result = local_tools.run_process(
            sys.executable,
            ["-c", "print('IN_PROCESS_CONTROL')"],
            root="pla",
            backend="in_process",
        )
        assert result["stdout"] == "IN_PROCESS_CONTROL\n"
        assert result["backend_preference"] == "in_process"
        assert result["resolved_backend_preference"] == "default"
        assert result["runner"]["process_isolation"] == "shared_http_process"
        assert "production" not in result
    finally:
        runner_runtime.stop_execution_runner()


def test_post_submit_indeterminate_write_never_blindly_falls_back(monkeypatch):
    request_id = "1" * 32

    def indeterminate(**kwargs):
        raise runner_runtime.RunnerExecutionIndeterminate(
            request_id,
            "simulated lost acknowledgement",
            {"runner_instance_id": "runner-x"},
        )

    monkeypatch.setattr(execution_backend, "run_production_oneshot", indeterminate, raising=False)
    # execution_backend imports the runtime function lazily, so patch the source module too.
    monkeypatch.setattr(runner_runtime, "run_production_oneshot", indeterminate)
    with pytest.raises(runner_runtime.RunnerExecutionIndeterminate, match=request_id):
        local_tools.run_process(
            sys.executable,
            ["-c", "print('MUST_NOT_FALLBACK')"],
            root="pla",
        )


def test_post_submit_indeterminate_repeatable_read_may_fallback(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    monkeypatch.setattr(local_tools, "WORKSPACE", repo.resolve())
    request_id = "2" * 32

    def indeterminate(**kwargs):
        raise runner_runtime.RunnerExecutionIndeterminate(
            request_id,
            "simulated result loss",
            {"runner_instance_id": "runner-y"},
        )

    monkeypatch.setattr(runner_runtime, "run_production_oneshot", indeterminate)
    result = local_tools.run_process(
        "git",
        ["rev-parse", "--is-inside-work-tree"],
        root="workspace",
    )
    assert result["stdout"].strip() == "true"
    assert result["runner"]["process_isolation"] == "shared_http_process"
    assert result["production"]["status"] == "fallback_indeterminate_read"
    assert result["production"]["execution_request_id"] == request_id


def test_submit_ack_loss_retries_same_request_id_without_double_execution(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AGENT_WORKSPACE", str(workspace.resolve()))
    monkeypatch.setattr(local_tools, "WORKSPACE", workspace.resolve())
    _redirect_runner_state(tmp_path, monkeypatch)
    runner_runtime.start_execution_runner()
    marker = workspace / "once.txt"
    original = RunnerSpikeClient.submit_resolved
    calls = {"count": 0}

    def flaky_submit(self, **kwargs):
        calls["count"] += 1
        response = original(self, **kwargs)
        if calls["count"] == 1:
            raise ConnectionError("simulated acknowledgement loss")
        return response

    monkeypatch.setattr(RunnerSpikeClient, "submit_resolved", flaky_submit)
    try:
        script = (
            "from pathlib import Path; "
            "p=Path('once.txt'); "
            "p.write_text((p.read_text() if p.exists() else '') + 'x', encoding='utf-8')"
        )
        result = runner_runtime.run_production_oneshot(
            command=[sys.executable, "-c", script],
            selected_root="workspace",
            cwd_relative=".",
            timeout=10,
            execution_request_id="3" * 32,
        )
        assert result["status"] == "completed"
        assert calls["count"] == 2
        assert marker.read_text(encoding="utf-8") == "x"
    finally:
        runner_runtime.stop_execution_runner()
