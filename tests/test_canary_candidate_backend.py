from pathlib import Path
import subprocess
import time

import local_tools
import execution_runner_runtime as runner_runtime
from command_semantics import classify_command
from promotion_execution_policy import evaluate_candidate_promotion
from task_store import TaskStore


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


def _redirect_runner_state(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "runner-state"
    monkeypatch.setattr(runner_runtime, "STATE_DIR", state_dir)
    monkeypatch.setattr(runner_runtime, "STATE_PATH", state_dir / "runtime.json")
    monkeypatch.setattr(runner_runtime, "AUTH_PATH", state_dir / "runner.auth")


def test_promotion_policy_only_promotes_repeatable_shadow_verified_reads():
    promoted = evaluate_candidate_promotion(
        classify_command("git", ["rev-parse", "HEAD"]),
        command=("git", "rev-parse", "HEAD"),
        selected_root="workspace",
        env_overrides=None,
        stdin=None,
        text=True,
    )
    assert promoted.promoted is True
    assert promoted.fallback_safe is True
    assert promoted.policy_id == "candidate_canary_readonly_git"

    python = evaluate_candidate_promotion(
        classify_command("python", ["-c", "print('x')"]),
        command=("python", "-c", "print('x')"),
        selected_root="workspace",
        env_overrides=None,
        stdin=None,
        text=True,
    )
    assert python.promoted is False
    assert python.fallback_safe is False


def test_canary_python_executes_only_default_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    marker = tmp_path / "marker.txt"
    script = (
        "from pathlib import Path; "
        f"p=Path({str(marker)!r}); "
        "p.write_text((p.read_text() if p.exists() else '') + 'x', encoding='utf-8')"
    )

    result = local_tools.run_process(
        "python",
        ["-c", script],
        root="workspace",
        backend="canary_candidate",
    )

    assert result["returncode"] == 0
    assert marker.read_text(encoding="utf-8") == "x"
    assert result["runner"]["backend"] == "in_process_windows"
    assert result["canary"]["status"] == "skipped"
    assert result["canary"]["policy"]["promoted"] is False


def test_canary_git_falls_back_when_runner_is_unavailable(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    monkeypatch.setattr(local_tools, "WORKSPACE", repo.resolve())
    _redirect_runner_state(tmp_path, monkeypatch)

    result = local_tools.run_process(
        "git",
        ["rev-parse", "--is-inside-work-tree"],
        root="workspace",
        backend="canary_candidate",
    )

    assert result["returncode"] == 0
    assert result["stdout"].strip() == "true"
    assert result["runner"]["backend"] == "in_process_windows"
    assert result["canary"]["status"] == "fallback"
    assert result["canary"]["policy"]["promoted"] is True
    assert result["canary"]["policy"]["fallback_safe"] is True
    assert result["canary"]["error_type"] == "RuntimeError"


def test_canary_git_prefers_live_candidate_runner(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    monkeypatch.setattr(local_tools, "WORKSPACE", repo.resolve())
    monkeypatch.setenv("AGENT_WORKSPACE", str(repo.resolve()))
    _redirect_runner_state(tmp_path, monkeypatch)

    started = runner_runtime.start_execution_runner()
    runner_id = started["runner"]["runner_instance_id"]
    try:
        result = local_tools.run_process(
            "git",
            ["rev-parse", "--is-inside-work-tree"],
            root="workspace",
            backend="canary_candidate",
        )
    finally:
        runner_runtime.stop_execution_runner()

    assert result["returncode"] == 0
    assert result["stdout"].strip() == "true"
    assert result["runner"]["backend"] == "named_pipe_candidate"
    assert result["runner"]["runner_instance_id"] == runner_id
    assert result["canary"]["status"] == "candidate"
    assert result["canary"]["candidate_runner"]["runner_instance_id"] == runner_id


def test_task_store_persists_canary_route_event(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    monkeypatch.setattr(local_tools, "WORKSPACE", repo.resolve())
    monkeypatch.setenv("AGENT_WORKSPACE", str(repo.resolve()))
    _redirect_runner_state(tmp_path, monkeypatch)

    runner_runtime.start_execution_runner()
    store = TaskStore(max_workers=1)
    try:
        submitted = store.submit({
            "tool": "run_process",
            "arguments": {
                "program": "git",
                "args": ["rev-parse", "--is-inside-work-tree"],
                "root": "workspace",
                "backend": "canary_candidate",
            },
        })
        task_id = submitted["task_id"]
        record = store.get(task_id, cursor=0, wait_seconds=5)
        while record["status"] not in {"completed", "failed", "cancelled"}:
            record = store.get(task_id, cursor=0, wait_seconds=1)
        assert record["status"] == "completed"
        events = [e for e in record["events"] if e["type"] == "execution_canary_routed"]
        assert len(events) == 1
        event = events[0]["data"]
        assert event["canary"]["status"] == "candidate"
        assert event["runner"]["backend"] == "named_pipe_candidate"
        assert "stdout" not in event["canary"]
        assert "stderr" not in event["canary"]
    finally:
        store.close()
        runner_runtime.stop_execution_runner()
