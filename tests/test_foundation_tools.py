import hashlib
import subprocess
from pathlib import Path

import pytest

import local_tools
from internal_tool_executor import execute_actions_request, execute_local_tool


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(local_tools, "WORKSPACE", root)
    return root


def test_search_text_returns_structured_matches(workspace):
    (workspace / "one.py").write_text("first\n# TODO here\n", encoding="utf-8")
    (workspace / "two.txt").write_text("TODO ignored by glob\n", encoding="utf-8")

    result = local_tools.search_text("todo", glob="*.py")

    assert result == {
        "status": "completed",
        "query": "todo",
        "path": ".",
        "matches": [{"path": "one.py", "line": 2, "text": "# TODO here"}],
        "match_count": 1,
        "truncated": False,
    }


def test_search_text_no_results(workspace):
    (workspace / "one.txt").write_text("nothing here\n", encoding="utf-8")
    result = local_tools.search_text("missing")
    assert result["matches"] == []
    assert result["match_count"] == 0
    assert result["truncated"] is False


def test_search_text_rejects_workspace_escape(workspace):
    with pytest.raises(ValueError, match="outside workspace"):
        local_tools.search_text("x", "../outside")


def test_search_text_truncates_at_global_limit(workspace):
    (workspace / "many.txt").write_text("hit\nhit\nhit\n", encoding="utf-8")
    result = local_tools.search_text("hit", max_results=2)
    assert result["match_count"] == 2
    assert len(result["matches"]) == 2
    assert result["truncated"] is True


def test_search_text_reports_missing_rg(workspace, monkeypatch):
    monkeypatch.setattr(local_tools.shutil, "which", lambda _name: None)
    with pytest.raises(local_tools.SearchToolUnavailable, match="ripgrep"):
        local_tools.search_text("x")


def test_execute_actions_can_search_text(workspace):
    (workspace / "one.txt").write_text("needle\n", encoding="utf-8")
    result = execute_actions_request([
        {"tool": "search_text", "arguments": {"query": "needle"}},
    ])
    assert result["status"] == "completed"
    assert result["results"][0]["result"]["match_count"] == 1


def test_run_process_default_workdir(workspace):
    result = local_tools.run_process(
        "python", ["-c", "import os; print(os.getcwd())"]
    )
    assert result["returncode"] == 0
    assert Path(result["stdout"].strip()).resolve() == workspace
    assert result["cwd"] == "."


def test_run_process_custom_workdir(workspace):
    child = workspace / "child"
    child.mkdir()
    result = local_tools.run_process(
        "python", ["-c", "import os; print(os.path.basename(os.getcwd()))"],
        workdir="child",
    )
    assert result["returncode"] == 0
    assert result["stdout"].strip() == "child"
    assert result["cwd"] == "child"


def test_run_process_rejects_outside_workdir(workspace):
    with pytest.raises(ValueError, match="outside workspace"):
        local_tools.run_process("python", workdir="../outside")


def test_run_process_env_override(workspace):
    result = local_tools.run_process(
        "python", ["-c", "import os; print(os.environ['SAFE_TEST_VALUE'])"],
        env={"SAFE_TEST_VALUE": "visible", "PYTHONUTF8": "1"},
    )
    assert result["stdout"].strip() == "visible"


def test_run_process_rejects_dangerous_env_override(workspace):
    with pytest.raises(ValueError, match="not allowed"):
        local_tools.run_process("python", env={"PATH": "elsewhere"})


def test_run_process_stdin(workspace):
    result = local_tools.run_process(
        "python", ["-c", "import sys; print(sys.stdin.read().upper())"],
        stdin="hello",
    )
    assert result["stdout"].strip() == "HELLO"


def test_run_process_timeout(workspace):
    result = execute_local_tool("run_process", {
        "program": "python", "args": ["-c", "import time; time.sleep(2)"],
        "timeout": 1,
    })
    assert result.ok is False
    assert result.error["type"] == "ProcessTimeout"
    assert result.result["timeout"] is True


def test_run_process_stderr_truncation(workspace):
    result = local_tools.run_process(
        "python", ["-c", "import sys; sys.stderr.write('e' * 21001)"],
    )
    assert result["stderr_truncated"] is True
    assert result["stderr_original_length"] == 21001
    assert len(result["stderr"]) == 20_000


def test_run_process_nonzero_returncode_is_structured(workspace):
    result = execute_local_tool("run_process", {
        "program": "python", "args": ["-c", "raise SystemExit(7)"],
    })
    assert result.ok is False
    assert result.result["returncode"] == 7
    assert result.error["type"] == "ProcessExitError"


def test_read_text_returns_file_metadata(workspace):
    raw = "hello\n".encode()
    (workspace / "a.txt").write_bytes(raw)
    result = local_tools.read_text("a.txt")
    assert result["sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["size"] == len(raw)
    assert result["mtime"].endswith("+00:00")


@pytest.mark.parametrize("tool", ["write_text", "replace_text"])
def test_mutation_with_correct_hash_succeeds(workspace, tool):
    target = workspace / "a.txt"
    target.write_text("old", encoding="utf-8")
    sha256 = local_tools.read_text("a.txt")["sha256"]
    if tool == "write_text":
        result = execute_local_tool(tool, {
            "path": "a.txt", "content": "new", "expected_sha256": sha256,
        })
    else:
        result = execute_local_tool(tool, {
            "path": "a.txt", "old": "old", "new": "new",
            "expected_sha256": sha256,
        })
    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "new"


@pytest.mark.parametrize("tool", ["write_text", "replace_text"])
def test_mutation_with_wrong_hash_preserves_file(workspace, tool):
    target = workspace / "a.txt"
    target.write_text("current", encoding="utf-8")
    arguments = {"path": "a.txt", "expected_sha256": "0" * 64}
    if tool == "write_text":
        arguments["content"] = "new"
    else:
        arguments.update({"old": "current", "new": "new"})
    result = execute_local_tool(tool, arguments)
    assert result.ok is False
    assert result.error["type"] == "FileChangedSinceRead"
    assert target.read_text(encoding="utf-8") == "current"


def test_apply_patch_updates_one_file_atomically(workspace):
    target = workspace / "a.txt"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    sha256 = local_tools.read_text("a.txt")["sha256"]
    result = local_tools.apply_patch(
        "a.txt", "@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n", sha256,
    )
    assert result["status"] == "completed"
    assert result["changed"] is True
    assert target.read_text(encoding="utf-8") == "one\nTWO\nthree\n"


def test_apply_patch_content_mismatch_preserves_file(workspace):
    target = workspace / "a.txt"
    target.write_text("actual\n", encoding="utf-8")
    with pytest.raises(local_tools.PatchApplyError, match="does not match"):
        local_tools.apply_patch("a.txt", "@@ -1 +1 @@\n-expected\n+new\n")
    assert target.read_text(encoding="utf-8") == "actual\n"


def test_apply_patch_rejects_workspace_escape(workspace):
    with pytest.raises(ValueError, match="outside workspace"):
        local_tools.apply_patch("../outside.txt", "@@ -1 +1 @@\n-a\n+b\n")


def test_apply_patch_hash_mismatch_preserves_file(workspace):
    target = workspace / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    result = execute_local_tool("apply_patch", {
        "path": "a.txt", "patch": "@@ -1 +1 @@\n-old\n+new\n",
        "expected_sha256": "f" * 64,
    })
    assert result.ok is False
    assert result.error["type"] == "FileChangedSinceRead"
    assert target.read_text(encoding="utf-8") == "old\n"


def test_apply_patch_is_not_allowed_in_execute_actions(workspace):
    (workspace / "a.txt").write_text("old\n", encoding="utf-8")
    result = execute_actions_request([{
        "tool": "apply_patch",
        "arguments": {"path": "a.txt", "patch": "@@ -1 +1 @@\n-old\n+new\n"},
    }])
    assert result["status"] == "error"
    assert result["results"][0]["error"]["type"] == "ToolNotAllowedInActions"


def test_run_powershell_allowlisted_cmdlet_succeeds_on_windows(workspace):
    (workspace / "a.txt").write_text("hello", encoding="utf-8")
    result = local_tools.run_powershell(
        "Get-ChildItem", {"LiteralPath": ".", "File": True}
    )
    assert result["status"] == "completed"
    assert result["returncode"] == 0
    assert "a.txt" in result["stdout"]


@pytest.mark.parametrize("command", [
    "Invoke-Expression", "Invoke-Command", "Invoke-WebRequest", "Start-Process",
    "Remove-Item", "cmd.exe", "Get-ChildItem; Remove-Item x",
    "Get-ChildItem | Remove-Item", "$(Get-Process)", "& cmd.exe",
])
def test_run_powershell_rejects_dangerous_commands(workspace, command):
    result = execute_local_tool("run_powershell", {"command": command})
    assert result.ok is False
    assert result.error["type"] == "PowerShellValidationError"


@pytest.mark.parametrize("path", ["../../outside", "C:\\\\", r"\\server\share"])
def test_run_powershell_rejects_outside_paths(workspace, path):
    result = execute_local_tool("run_powershell", {
        "command": "Get-ChildItem", "parameters": {"LiteralPath": path},
    })
    assert result.ok is False
    assert "outside workspace" in result.error["message"]


@pytest.mark.parametrize("payload", [
    "; Remove-Item marker.txt", "| Remove-Item marker.txt", "$(Get-Process)",
    "& cmd.exe", ". ./payload.ps1",
])
def test_run_powershell_parameter_payload_is_literal_data(workspace, payload):
    marker = workspace / "marker.txt"
    marker.write_text("safe", encoding="utf-8")
    result = execute_local_tool("run_powershell", {
        "command": "Get-ChildItem",
        "parameters": {"LiteralPath": payload, "File": True},
    })
    assert result.ok is False
    assert marker.read_text(encoding="utf-8") == "safe"


def test_run_powershell_does_not_accept_script_argument(workspace):
    result = execute_local_tool("run_powershell", {
        "command": "Get-Date", "script": "Remove-Item marker.txt",
    })
    assert result.ok is False
    assert result.error["type"] == "TypeError"


def test_run_powershell_rejects_unlisted_parameter(workspace):
    result = execute_local_tool("run_powershell", {
        "command": "Get-Date", "parameters": {"OutVariable": "payload"},
    })
    assert result.ok is False
    assert result.error["type"] == "PowerShellValidationError"


def test_run_powershell_timeout_is_structured(workspace, monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("powershell", 1, output=b"partial")

    monkeypatch.setattr(local_tools.subprocess, "run", timeout)
    result = execute_local_tool("run_powershell", {
        "command": "Get-Date", "timeout": 1,
    })
    assert result.ok is False
    assert result.error["type"] == "ProcessTimeout"
    assert result.result["timeout"] is True


def test_run_powershell_output_truncation(workspace):
    (workspace / "large.txt").write_text("x" * 21_000, encoding="utf-8")
    result = local_tools.run_powershell(
        "Get-Content", {"LiteralPath": "large.txt", "Raw": True}
    )
    assert result["status"] == "completed"
    assert result["stdout_truncated"] is True
    assert result["stdout_original_length"] > 20_000
    assert len(result["stdout"]) == 20_000


def test_run_powershell_is_not_allowed_in_execute_actions(workspace):
    result = execute_actions_request([{
        "tool": "run_powershell", "arguments": {"command": "Get-Date"},
    }])
    assert result["status"] == "error"
    assert result["results"][0]["error"]["type"] == "ToolNotAllowedInActions"
