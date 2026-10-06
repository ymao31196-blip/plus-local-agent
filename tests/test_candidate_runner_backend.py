import sys
from pathlib import Path

import pytest

import execution_runner_runtime as runner_runtime
import local_tools


def _redirect_runner_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(runner_runtime, "STATE_DIR", tmp_path)
    monkeypatch.setattr(runner_runtime, "STATE_PATH", tmp_path / "runtime.json")
    monkeypatch.setattr(runner_runtime, "AUTH_PATH", tmp_path / "runner.auth")


def test_candidate_backend_is_opt_in_and_matches_basic_oneshot(tmp_path, monkeypatch):
    _redirect_runner_state(tmp_path, monkeypatch)
    started = runner_runtime.start_execution_runner()
    runner_id = started["runner"]["runner_instance_id"]
    runner_pid = started["runner"]["process_id"]
    try:
        default = local_tools.run_process(
            sys.executable,
            ["-c", "print('BACKEND_PARITY_OK')"],
            root="pla",
            backend="in_process",
        )
        candidate = local_tools.run_process(
            sys.executable,
            ["-c", "print('BACKEND_PARITY_OK')"],
            root="pla",
            backend="candidate_runner",
        )
        assert default["stdout"] == candidate["stdout"] == "BACKEND_PARITY_OK\n"
        assert default["returncode"] == candidate["returncode"] == 0
        assert default["backend_preference"] == "in_process"
        assert default["resolved_backend_preference"] == "default"
        assert candidate["backend_preference"] == "candidate_runner"
        assert default["runner"]["process_isolation"] == "shared_http_process"
        assert candidate["runner"]["process_isolation"] == "separate_process"
        assert candidate["runner"]["runner_instance_id"] == runner_id
        assert candidate["runner"]["process_id"] == runner_pid
    finally:
        runner_runtime.stop_execution_runner()


def test_candidate_backend_preserves_validated_env_overrides(tmp_path, monkeypatch):
    _redirect_runner_state(tmp_path, monkeypatch)
    runner_runtime.start_execution_runner()
    try:
        result = local_tools.run_process(
            sys.executable,
            ["-c", "import os; print(os.environ['PLA_PHASE_O_ENV'])"],
            root="pla",
            env={"PLA_PHASE_O_ENV": "candidate-ok"},
            backend="candidate_runner",
        )
        assert result["stdout"] == "candidate-ok\n"
        assert result["runner"]["backend"] == "named_pipe_candidate"
    finally:
        runner_runtime.stop_execution_runner()


def test_candidate_backend_requires_running_runner(tmp_path, monkeypatch):
    _redirect_runner_state(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="candidate execution runner is not running"):
        local_tools.run_process(
            sys.executable,
            ["-c", "print('never-runs')"],
            root="pla",
            backend="candidate_runner",
        )


def test_semantic_steering_precedes_candidate_backend_selection(tmp_path, monkeypatch):
    _redirect_runner_state(tmp_path, monkeypatch)
    # No candidate Runner is started. If backend selection happened before steering,
    # this would fail with "candidate execution runner is not running" instead.
    blocked = local_tools.run_process(
        "git",
        ["push", "origin", "master"],
        root="workspace",
        backend="candidate_runner",
    )
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "specialized_capability_required"
    assert blocked["suggested_capabilities"][0]["name"] == "core.git_push"
