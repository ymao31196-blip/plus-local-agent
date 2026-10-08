from pathlib import Path
import subprocess
import time

from tooling import local_tools
from execution import execution_runner_runtime as runner_runtime
from execution.command_semantics import classify_command
from execution.shadow_execution_policy import evaluate_shadow_execution
from runtime.task_store import TaskStore


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


def test_shadow_policy_is_strictly_local_readonly_git():
    allowed = evaluate_shadow_execution(
        classify_command("git", ["rev-parse", "HEAD"]),
        command=("git", "rev-parse", "HEAD"),
        selected_root="workspace",
        env_overrides=None,
        stdin=None,
        text=True,
    )
    assert allowed.eligible is True
    assert allowed.reason == "strict_local_readonly_git"

    python = evaluate_shadow_execution(
        classify_command("python", ["-c", "print('x')"]),
        command=("python", "-c", "print('x')"),
        selected_root="workspace",
        env_overrides=None,
        stdin=None,
        text=True,
    )
    assert python.eligible is False
    assert python.reason == "domain_not_shadowable"

    override = evaluate_shadow_execution(
        classify_command("git", ["-C", "other", "rev-parse", "HEAD"]),
        command=("git", "-C", "other", "rev-parse", "HEAD"),
        selected_root="workspace",
        env_overrides=None,
        stdin=None,
        text=True,
    )
    assert override.eligible is False
    assert override.reason == "git_context_override_not_shadowable"


def test_shadow_python_executes_primary_once_and_is_skipped(tmp_path, monkeypatch):
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
        backend="shadow_candidate",
    )

    assert result["returncode"] == 0
    assert marker.read_text(encoding="utf-8") == "x"
    assert result["runner"]["backend"] == "in_process_windows"
    assert result["shadow"]["status"] == "skipped"
    assert result["shadow"]["policy"]["reason"] == "domain_not_shadowable"


def test_shadow_git_returns_primary_when_candidate_is_unavailable(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    monkeypatch.setattr(local_tools, "WORKSPACE", repo.resolve())
    _redirect_runner_state(tmp_path, monkeypatch)

    result = local_tools.run_process(
        "git",
        ["rev-parse", "--is-inside-work-tree"],
        root="workspace",
        backend="shadow_candidate",
    )

    assert result["returncode"] == 0
    assert result["stdout"].strip() == "true"
    assert result["runner"]["backend"] == "in_process_windows"
    assert result["shadow"]["status"] == "unavailable"
    assert result["shadow"]["policy"]["eligible"] is True
    assert result["shadow"]["error_type"] == "RuntimeError"


def test_shadow_git_matches_live_candidate_without_replacing_primary(tmp_path, monkeypatch):
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
            backend="shadow_candidate",
        )
    finally:
        runner_runtime.stop_execution_runner()

    assert result["returncode"] == 0
    assert result["stdout"].strip() == "true"
    assert result["runner"]["backend"] == "in_process_windows"
    shadow = result["shadow"]
    assert shadow["status"] == "matched"
    assert shadow["returncode_match"] is True
    assert shadow["stdout_match"] is True
    assert shadow["stderr_match"] is True
    assert shadow["shadow_runner"]["runner_instance_id"] == runner_id
    assert shadow["shadow_runner"]["backend"] == "named_pipe_candidate"
    assert "content" not in shadow["primary"]["stdout"]
    assert set(shadow["primary"]["stdout"]) == {"bytes", "sha256"}


def test_task_store_persists_redacted_shadow_comparison_event(tmp_path, monkeypatch):
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
                "backend": "shadow_candidate",
            },
        })
        deadline = time.monotonic() + 5
        record = store.get(submitted["task_id"], cursor=0, wait_seconds=1)
        while (
            record["status"] not in {"completed", "failed", "cancelled"}
            and time.monotonic() < deadline
        ):
            record = store.get(submitted["task_id"], cursor=0, wait_seconds=1)
        assert record["status"] == "completed"
        shadow_events = [
            event for event in record["events"]
            if event["type"] == "execution_shadow_compared"
        ]
        assert len(shadow_events) == 1
        shadow = shadow_events[0]["data"]["shadow"]
        assert shadow["status"] == "matched"
        assert set(shadow["primary"]["stdout"]) == {"bytes", "sha256"}
        assert "content" not in shadow["primary"]["stdout"]
    finally:
        store.close()
        runner_runtime.stop_execution_runner()
