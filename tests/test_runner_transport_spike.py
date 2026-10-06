import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from runner_transport import (
    RunnerSpikeClient,
    SPIKE_PROTOCOL_VERSION,
    make_pipe_address,
    read_auth_file,
    wait_for_runner_state,
    write_auth_file,
)


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows named-pipe spike")


def _wait_stopped(state_file: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if state_file.exists():
            data = json.loads(state_file.read_text(encoding="utf-8"))
            if data.get("status") == "stopped":
                return
        time.sleep(0.05)
    raise AssertionError("runner spike did not stop")


def test_detached_runner_survives_launcher_and_runs_fixed_probe(tmp_path):
    auth_file = tmp_path / "runner.auth"
    state_file = tmp_path / "runner-state.json"
    write_auth_file(auth_file)
    address = make_pipe_address()
    repo = Path(__file__).resolve().parents[1]

    code = (
        "from pathlib import Path; "
        "from runner_transport import start_detached_runner; "
        f"start_detached_runner(address={address!r}, "
        f"auth_file=Path({str(auth_file)!r}), state_file=Path({str(state_file)!r}))"
    )
    launcher = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=repo,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    launcher_pid = launcher.pid
    stdout, stderr = launcher.communicate(timeout=10)
    assert launcher.returncode == 0, (stdout, stderr)

    state = wait_for_runner_state(state_file, timeout=10)
    runner_pid = state["process_id"]
    assert runner_pid != launcher_pid
    assert runner_pid != os.getpid()
    assert state["process_isolation"] == "separate_process"
    assert state["backend"] == "named_pipe_candidate"

    client = RunnerSpikeClient(address, read_auth_file(auth_file))
    try:
        # The launcher has already exited; the runner must still accept work.
        ping = client.ping()
        assert ping["status"] == "ok"
        assert ping["runner"]["process_id"] == runner_pid

        result = client.probe()
        assert result["status"] == "completed"
        assert result["returncode"] == 0
        assert "PLA_EXECUTION_RUNNER_PROBE_OK" in result["stdout"]["content"]
        assert result["runner"]["runner_instance_id"] == state["runner_instance_id"]
        assert result["runner"]["scope"] == "opt_in_text_oneshot"
        assert isinstance(result["child_pid"], int)
        assert result["child_pid"] > 0
        assert result["child_pid"] != runner_pid

        resolved = client.run_resolved(
            command=[
                sys.executable,
                "-c",
                "import os; print('OPT_IN_RUNNER_OK:' + os.environ['PLA_PHASE_O_TEST'])",
            ],
            selected_root="pla",
            cwd_relative=".",
            timeout=10,
            env_overrides={"PLA_PHASE_O_TEST": "yes"},
        )
        assert resolved["status"] == "completed"
        assert resolved["returncode"] == 0
        assert "OPT_IN_RUNNER_OK:yes" in resolved["stdout"]["content"]
        assert resolved["runner"]["process_id"] == runner_pid
        assert resolved["semantic"]["action"] == "inline_code"

        blocked_semantic = client.run_resolved(
            command=[sys.executable, "-m", "pip", "install", "example"],
            selected_root="pla",
            cwd_relative=".",
            timeout=10,
        )
        assert blocked_semantic["status"] == "error"
        assert "semantic confirmation" in blocked_semantic["message"]

        blocked_git = client.run_resolved(
            command=["git", "status"],
            selected_root="pla",
            cwd_relative=".",
            timeout=10,
        )
        assert blocked_git["status"] == "error"
        assert "PLA source root" in blocked_git["message"]

        blocked_escape = client.run_resolved(
            command=[sys.executable, "-c", "print('SHOULD_NOT_RUN')"],
            selected_root="pla",
            cwd_relative="..",
            timeout=10,
        )
        assert blocked_escape["status"] == "error"

        rejected = client.request(
            {
                "op": "run_oneshot",
                "protocol_version": SPIKE_PROTOCOL_VERSION,
                "command": [sys.executable, "-c", "print('SHOULD_NOT_RUN')"],
            }
        )
        assert rejected["status"] == "error"
        assert "unsupported runner operation" in rejected["message"]
    finally:
        try:
            client.shutdown()
            _wait_stopped(state_file)
        except Exception:
            # Last-resort cleanup for a failed spike test; never accepts a caller PID in
            # production code.  This test owns the exact PID created for its runner.
            try:
                os.kill(int(runner_pid), signal.SIGTERM)
            except Exception:
                pass
