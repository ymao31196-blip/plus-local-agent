import hashlib
import socket
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


def test_search_text_falls_back_when_rg_missing(workspace, monkeypatch):
    monkeypatch.setattr(local_tools.shutil, "which", lambda _name: None)
    (workspace / "one.txt").write_text("Alpha\nneedle here\n", encoding="utf-8")

    result = local_tools.search_text("NEEDLE")

    assert result["matches"] == [
        {"path": "one.txt", "line": 2, "text": "needle here"}
    ]
    assert result["match_count"] == 1
    assert result["truncated"] is False


def test_execute_actions_can_search_text(workspace):
    (workspace / "one.txt").write_text("needle\n", encoding="utf-8")
    result = execute_actions_request([
        {"tool": "search_text", "arguments": {"query": "needle"}},
    ])
    assert result["status"] == "completed"
    assert result["results"][0]["result"]["match_count"] == 1


def _init_git_repo(path: Path) -> str:
    git = local_tools.shutil.which("git")
    if git is None:
        pytest.skip("git executable is unavailable")
    subprocess.run([git, "init", "-q"], cwd=path, check=True)
    (path / "tracked.txt").write_text("one\n", encoding="utf-8")
    subprocess.run([git, "add", "tracked.txt"], cwd=path, check=True)
    subprocess.run(
        [
            git,
            "-c", "user.name=PLA Test",
            "-c", "user.email=pla@example.invalid",
            "commit", "-qm", "initial",
        ],
        cwd=path,
        check=True,
    )
    subprocess.run([git, "config", "user.name", "PLA Test"], cwd=path, check=True)
    subprocess.run(
        [git, "config", "user.email", "pla@example.invalid"], cwd=path, check=True,
    )
    return git


def test_structured_git_status_and_diff(workspace):
    git = _init_git_repo(workspace)
    (workspace / "tracked.txt").write_text("two\n", encoding="utf-8")
    (workspace / "new.txt").write_text("new\n", encoding="utf-8")

    status = local_tools.git_status()
    files = {item["path"]: item for item in status["files"]}

    assert status["status"] == "completed"
    assert status["repo_root"] == "."
    assert status["head"]
    assert status["clean"] is False
    assert files["tracked.txt"]["worktree_status"] == "M"
    assert files["new.txt"]["code"] == "??"

    diff = local_tools.git_diff(path="tracked.txt")
    assert diff["staged"] is False
    assert "-one" in diff["diff"] and "+two" in diff["diff"]
    assert diff["truncated"] is False

    subprocess.run([git, "add", "tracked.txt"], cwd=workspace, check=True)
    staged = local_tools.git_diff(staged=True, path="tracked.txt")
    assert staged["staged"] is True
    assert "+two" in staged["diff"]



def test_structured_git_log_and_show(workspace):
    git = _init_git_repo(workspace)
    (workspace / "tracked.txt").write_text("two\n", encoding="utf-8")
    subprocess.run([git, "add", "tracked.txt"], cwd=workspace, check=True)
    subprocess.run(
        [git, "-c", "user.name=PLA Test", "-c", "user.email=pla@example.invalid",
         "commit", "-qm", "second"],
        cwd=workspace, check=True,
    )

    history = local_tools.git_log(limit=1)
    assert history["entry_count"] == 1
    assert history["entries"][0]["subject"] == "second"
    assert history["truncated"] is True

    shown = local_tools.git_show(path="tracked.txt")
    assert shown["revision"] == "HEAD"
    assert shown["subject"] == "second"
    assert shown["commit"] == history["entries"][0]["commit"]
    assert "-one" in shown["patch"] and "+two" in shown["patch"]
    assert shown["truncated"] is False


def test_git_show_rejects_option_like_revision(workspace):
    _init_git_repo(workspace)
    with pytest.raises(ValueError, match="revision is not allowed"):
        local_tools.git_show("--help")

def test_structured_git_stage_then_commit(workspace):
    _init_git_repo(workspace)
    before = local_tools.git_status()["head"]
    (workspace / "tracked.txt").write_text("two\n", encoding="utf-8")
    sha = local_tools.read_text("tracked.txt")["sha256"]

    staged = local_tools.git_stage(
        [{"path": "tracked.txt", "expected_sha256": sha}], before
    )

    assert staged["status"] == "completed"
    assert staged["head"] == before
    assert staged["paths"] == ["tracked.txt"]
    assert staged["preflight"]["mode"] == "raw_bytes_explicit_files"
    status = local_tools.git_status()
    tracked = {item["path"]: item for item in status["files"]}["tracked.txt"]
    assert tracked["index_status"] == "M"
    assert tracked["worktree_status"] == " "

    committed = local_tools.git_commit("stage then commit", ["tracked.txt"], before)
    assert committed["previous_head"] == before
    assert local_tools.git_status()["head"] == committed["commit"]


def test_structured_git_stage_rejects_wrong_hash_without_index_change(workspace):
    _init_git_repo(workspace)
    before = local_tools.git_status()["head"]
    (workspace / "tracked.txt").write_text("two\n", encoding="utf-8")

    with pytest.raises(local_tools.FileChangedSinceRead):
        local_tools.git_stage(
            [{"path": "tracked.txt", "expected_sha256": "0" * 64}], before
        )
    assert local_tools.git_diff(staged=True)["diff"] == ""


def test_structured_git_stage_rejects_existing_staged_changes(workspace):
    git = _init_git_repo(workspace)
    before = local_tools.git_status()["head"]
    (workspace / "tracked.txt").write_text("two\n", encoding="utf-8")
    subprocess.run([git, "add", "tracked.txt"], cwd=workspace, check=True)
    sha = local_tools.read_text("tracked.txt")["sha256"]

    with pytest.raises(ValueError, match="already contains staged changes"):
        local_tools.git_stage(
            [{"path": "tracked.txt", "expected_sha256": sha}], before
        )


def test_structured_git_stage_and_commit_new_file(workspace):
    _init_git_repo(workspace)
    before = local_tools.git_status()["head"]
    (workspace / "new.txt").write_text("new\n", encoding="utf-8")
    sha = local_tools.read_text("new.txt")["sha256"]

    staged = local_tools.git_stage(
        [{"path": "new.txt", "expected_sha256": sha}], before
    )

    assert staged["status"] == "completed"
    assert staged["paths"] == ["new.txt"]
    assert staged["preflight"]["new_files_allowed"] is True
    status = local_tools.git_status()
    new_file = {item["path"]: item for item in status["files"]}["new.txt"]
    assert new_file["index_status"] == "A"
    assert new_file["worktree_status"] == " "

    committed = local_tools.git_commit("add new file", ["new.txt"], before)
    assert committed["previous_head"] == before
    assert local_tools.git_status()["head"] == committed["commit"]
    shown = local_tools.git_show(committed["commit"], path="new.txt")
    assert "+new" in shown["patch"]


def test_structured_git_stage_rejects_ignored_untracked_file(workspace):
    git = _init_git_repo(workspace)
    before = local_tools.git_status()["head"]
    (workspace / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    subprocess.run(
        [git, "add", ".gitignore"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [git, "commit", "-m", "ignore fixture"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    before = local_tools.git_status()["head"]
    (workspace / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    sha = hashlib.sha256((workspace / "ignored.txt").read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="refuses ignored"):
        local_tools.git_stage(
            [{"path": "ignored.txt", "expected_sha256": sha}], before
        )
    assert local_tools.git_diff(staged=True)["diff"] == ""


def test_structured_git_commit_exact_staged_tracked_file(workspace):
    git = _init_git_repo(workspace)
    before = local_tools.git_status()["head"]
    (workspace / "tracked.txt").write_text("two\n", encoding="utf-8")
    subprocess.run([git, "add", "tracked.txt"], cwd=workspace, check=True)

    result = local_tools.git_commit("structured commit", ["tracked.txt"], before)

    assert result["status"] == "completed"
    assert result["previous_head"] == before
    assert result["commit"] == local_tools.git_status()["head"]
    assert result["paths"] == ["tracked.txt"]
    assert result["preflight"]["mode"] == "staged_explicit_files"
    shown = local_tools.git_show(result["commit"], path="tracked.txt")
    assert shown["subject"] == "structured commit"
    assert shown["parents"] == [before]
    assert "+two" in shown["patch"]
    assert local_tools.git_diff(staged=True)["diff"] == ""


def test_structured_git_commit_rejects_unexpected_staged_paths(workspace):
    git = _init_git_repo(workspace)
    (workspace / "other.txt").write_text("base\n", encoding="utf-8")
    subprocess.run([git, "add", "other.txt"], cwd=workspace, check=True)
    subprocess.run([git, "commit", "-qm", "add other"], cwd=workspace, check=True)
    before = local_tools.git_status()["head"]
    (workspace / "tracked.txt").write_text("two\n", encoding="utf-8")
    (workspace / "other.txt").write_text("changed\n", encoding="utf-8")
    subprocess.run([git, "add", "tracked.txt", "other.txt"], cwd=workspace, check=True)

    with pytest.raises(ValueError, match="Staged paths must exactly match"):
        local_tools.git_commit("only one", ["tracked.txt"], before)
    assert local_tools.git_status()["head"] == before


def test_structured_git_commit_rejects_stale_head_and_unstaged_selected_content(workspace):
    git = _init_git_repo(workspace)
    before = local_tools.git_status()["head"]
    (workspace / "tracked.txt").write_text("two\n", encoding="utf-8")
    subprocess.run([git, "add", "tracked.txt"], cwd=workspace, check=True)

    with pytest.raises(ValueError, match="HEAD changed since inspection"):
        local_tools.git_commit("stale", ["tracked.txt"], "0" * len(before))
    assert local_tools.git_status()["head"] == before

    (workspace / "tracked.txt").write_text("three\n", encoding="utf-8")
    with pytest.raises(ValueError, match="still have unstaged changes"):
        local_tools.git_commit("stale content", ["tracked.txt"], before)
    assert local_tools.git_status()["head"] == before


def test_structured_git_commit_accepts_exact_newly_added_file(workspace):
    git = _init_git_repo(workspace)
    before = local_tools.git_status()["head"]
    (workspace / "new.txt").write_text("new\n", encoding="utf-8")
    subprocess.run([git, "add", "new.txt"], cwd=workspace, check=True)

    committed = local_tools.git_commit("new file", ["new.txt"], before)

    assert committed["previous_head"] == before
    assert local_tools.git_status()["head"] == committed["commit"]
    shown = local_tools.git_show(committed["commit"], path="new.txt")
    assert "+new" in shown["patch"]



def test_controlled_git_tag_creates_exact_lightweight_tag(workspace):
    git = _init_git_repo(workspace)
    head = local_tools.git_status()["head"]

    result = local_tools.git_tag("v1.2.3", head)

    assert result["status"] == "completed"
    assert result["head"] == head
    assert result["tag"] == "v1.2.3"
    assert result["tag_commit"] == head
    resolved = subprocess.run(
        [git, "rev-parse", "refs/tags/v1.2.3^{commit}"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert resolved == head

    with pytest.raises(ValueError, match="already exists"):
        local_tools.git_tag("v1.2.3", head)


def test_controlled_git_tag_requires_clean_repo(workspace):
    _init_git_repo(workspace)
    head = local_tools.git_status()["head"]
    (workspace / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(ValueError, match="clean worktree"):
        local_tools.git_tag("v1.2.3", head)


def test_controlled_git_push_branch_and_tag_to_configured_remote(workspace):
    git = _init_git_repo(workspace)
    head = local_tools.git_status()["head"]
    branch = subprocess.run(
        [git, "symbolic-ref", "--short", "HEAD"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    remote = workspace.parent / "remote.git"
    subprocess.run([git, "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(
        [git, "remote", "add", "origin", str(remote)],
        cwd=workspace,
        check=True,
    )

    local_tools.git_tag("v1.2.3", head)

    with pytest.raises(PermissionError, match="confirmation='PUSH'"):
        local_tools.git_push(
            "origin", branch, head, ["v1.2.3"]
        )

    result = local_tools.git_push(
        "origin",
        branch,
        head,
        ["v1.2.3"],
        confirmation="PUSH",
    )

    assert result["status"] == "completed"
    assert result["head"] == head
    assert result["branch"] == branch
    assert result["tags"] == ["v1.2.3"]
    assert result["preflight"]["atomic_push"] is True
    assert result["preflight"]["force_disabled"] is True
    assert result["preflight"]["remote_refs_verified"] is True

    remote_branch = subprocess.run(
        [git, "--git-dir", str(remote), "rev-parse", f"refs/heads/{branch}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    remote_tag = subprocess.run(
        [git, "--git-dir", str(remote), "rev-parse", "refs/tags/v1.2.3^{commit}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert remote_branch == head
    assert remote_tag == head


def test_structured_git_tools_are_batchable_read_only(workspace):
    _init_git_repo(workspace)
    (workspace / "tracked.txt").write_text("changed\n", encoding="utf-8")
    result = execute_actions_request([
        {"tool": "git_status", "arguments": {}},
        {"tool": "git_diff", "arguments": {"path": "tracked.txt"}},
        {"tool": "git_log", "arguments": {"limit": 1}},
        {"tool": "git_show", "arguments": {}},
    ])
    assert result["status"] == "completed"
    assert result["results"][0]["result"]["clean"] is False
    assert "+changed" in result["results"][1]["result"]["diff"]
    assert result["results"][2]["result"]["entry_count"] == 1
    assert result["results"][3]["result"]["subject"] == "initial"


def test_git_status_rejects_repository_root_outside_selected_root(tmp_path, monkeypatch):
    outer = tmp_path / "outer"
    outer.mkdir()
    _init_git_repo(outer)
    child = outer / "child"
    child.mkdir()
    monkeypatch.setattr(local_tools, "WORKSPACE", child.resolve())

    with pytest.raises(ValueError, match="repository root is outside"):
        local_tools.git_status()


def test_git_diff_rejects_path_outside_selected_repository(workspace):
    repo = workspace / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (workspace / "outside.txt").write_text("outside\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside the selected Git repository"):
        local_tools.git_diff(cwd="repo", path="outside.txt")


def test_run_process_default_workdir(workspace):
    result = local_tools.run_process(
        "python", ["-c", "import os; print(os.getcwd())"]
    )
    assert result["returncode"] == 0
    assert Path(result["stdout"].strip()).resolve() == workspace
    assert result["cwd"] == "."


def test_run_process_allowlist_includes_github_cli():
    assert {"gh", "gh.exe"}.issubset(local_tools.ALLOWED_PROGRAMS)


def test_run_process_pla_git_returns_specialized_capability_steering():
    result = local_tools.run_process(
        "git", ["push", "origin", "master"], root="pla"
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "specialized_capability_required"
    assert result["routing_mode"] == "specialized_enforced"
    assert result["attempted_route"]["root"] == "pla"
    assert result["suggested_capabilities"][0]["name"] == "core.git_push"


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


def test_extract_document_text_reads_text_ranges_and_metadata(workspace):
    raw = "alpha\nbeta\ngamma\n".encode()
    (workspace / "notes.md").write_bytes(raw)

    result = local_tools.extract_document_text(
        "notes.md", start=2, end=3, max_chars=100,
    )

    assert result["format"] == "text"
    assert result["unit"] == "line"
    assert result["unit_count"] == 3
    assert result["selected_start"] == 2
    assert result["selected_end"] == 3
    assert result["content"] == "beta\ngamma"
    assert result["truncated"] is False
    assert result["sha256"] == hashlib.sha256(raw).hexdigest()


def test_extract_document_text_reads_docx_without_external_word_dependency(workspace):
    import zipfile

    target = workspace / "sample.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Alpha paragraph</w:t></w:r></w:p>
    <w:p><w:r><w:t>Beta paragraph</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    result = local_tools.extract_document_text("sample.docx", start=2, end=2)

    assert result["format"] == "docx"
    assert result["unit"] == "paragraph"
    assert result["unit_count"] == 2
    assert result["content"] == "Beta paragraph"


def test_extract_document_text_reads_pdf_pages(workspace):
    from pypdf import PdfWriter

    target = workspace / "sample.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=72, height=72)
    with target.open("wb") as handle:
        writer.write(handle)

    result = local_tools.extract_document_text("sample.pdf", start=2, end=2)

    assert result["format"] == "pdf"
    assert result["unit"] == "page"
    assert result["unit_count"] == 2
    assert result["selected_start"] == 2
    assert result["selected_end"] == 2
    assert result["content"].startswith("[Page 2]")


def test_extract_document_text_bounds_output_and_rejects_unknown_types(workspace):
    (workspace / "notes.txt").write_text("abcdefghij", encoding="utf-8")
    bounded = local_tools.extract_document_text("notes.txt", max_chars=4)
    assert bounded["content"] == "abcd"
    assert bounded["truncated"] is True
    assert bounded["original_length"] == 10

    (workspace / "binary.bin").write_bytes(b"abc")
    with pytest.raises(ValueError, match="Unsupported document type"):
        local_tools.extract_document_text("binary.bin")


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
    assert isinstance(result["data"], list)
    assert result["data"][0]["Name"] == "a.txt"
    assert result["data_truncated"] is False
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


def test_run_powershell_service_write_returns_specialized_capability_steering():
    result = local_tools.run_powershell(
        "Restart-Service",
        {"Name": "Spooler"},
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "specialized_capability_required"
    assert result["domain"] == "windows_service"
    assert result["attempted_route"]["operation"] == "restart"
    assert result["attempted_route"]["service_name"] == "Spooler"
    assert result["suggested_capabilities"][0]["name"] == (
        "windows.service_control_preflight"
    )


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


def test_run_powershell_returns_structured_command_observation(workspace):
    result = local_tools.run_powershell("Get-Command", {"Name": "python"})
    assert result["status"] == "completed"
    assert result["stderr"] == ""
    assert result["data_truncated"] is False
    assert isinstance(result["data"], list)
    assert result["data"]
    record = result["data"][0]
    assert set(record) == {"Name", "CommandType", "Source", "Version"}
    assert record["Name"].casefold().startswith("python")


def test_run_powershell_clixml_error_is_normalized(workspace):
    result = local_tools.run_powershell("Get-Process", {"Id": 2147483647})
    assert result["status"] == "error"
    assert "#< CLIXML" not in result["stderr"]
    assert "Cannot find a process" in result["stderr"]


def test_run_powershell_get_service_is_structured(workspace):
    result = local_tools.run_powershell("Get-Service", {"Name": "EventLog"})
    assert result["status"] == "completed"
    assert result["data_truncated"] is False
    assert result["data"]
    record = result["data"][0]
    assert record["Name"] == "EventLog"
    assert set(record) == {"Name", "DisplayName", "Status", "StartType"}


def test_run_powershell_get_net_tcp_connection_local_port(workspace):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        port = listener.getsockname()[1]
        result = local_tools.run_powershell(
            "Get-NetTCPConnection", {"LocalPort": port, "State": "Listen"}
        )
    finally:
        listener.close()
    assert result["status"] == "completed"
    assert result["data_truncated"] is False
    assert any(
        row["LocalPort"] == port and row["State"] == "Listen"
        for row in result["data"]
    )


def test_run_powershell_get_net_udp_endpoint_local_port(workspace):
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", 0))
    try:
        port = listener.getsockname()[1]
        result = local_tools.run_powershell(
            "Get-NetUDPEndpoint", {"LocalPort": port}
        )
    finally:
        listener.close()
    assert result["status"] == "completed"
    assert result["data_truncated"] is False
    assert any(row["LocalPort"] == port for row in result["data"])
    assert set(result["data"][0]) == {"LocalAddress", "LocalPort", "OwningProcess"}


def test_run_powershell_get_net_adapter_is_structured(workspace):
    result = local_tools.run_powershell("Get-NetAdapter")
    assert result["status"] == "completed"
    assert result["data_truncated"] is False
    assert result["data"]
    assert set(result["data"][0]) == {
        "Name", "InterfaceDescription", "InterfaceIndex",
        "Status", "MacAddress", "LinkSpeed",
    }


def test_run_powershell_get_net_ip_configuration_is_structured(workspace):
    result = local_tools.run_powershell("Get-NetIPConfiguration")
    assert result["status"] == "completed"
    assert result["data_truncated"] is False
    assert result["data"]
    assert set(result["data"][0]) == {
        "InterfaceAlias", "InterfaceIndex", "IPv4Address", "IPv6Address",
        "IPv4DefaultGateway", "IPv6DefaultGateway",
    }
    for row in result["data"]:
        assert None not in row["IPv4Address"]
        assert None not in row["IPv6Address"]
        assert None not in row["IPv4DefaultGateway"]
        assert None not in row["IPv6DefaultGateway"]


def test_run_powershell_get_dns_client_server_address_is_structured(workspace):
    result = local_tools.run_powershell(
        "Get-DnsClientServerAddress", {"AddressFamily": "IPv4"}
    )
    assert result["status"] == "completed"
    assert result["data_truncated"] is False
    assert result["data"]
    assert set(result["data"][0]) == {
        "InterfaceAlias", "InterfaceIndex", "AddressFamily", "ServerAddresses",
    }
    assert all(row["AddressFamily"] == "IPv4" for row in result["data"])


@pytest.mark.parametrize("parameters", [
    {"LocalPort": 65536},
    {"RemotePort": 70000},
    {"OwningProcess": 0},
])
def test_run_powershell_rejects_invalid_tcp_numeric_parameters(workspace, parameters):
    result = execute_local_tool("run_powershell", {
        "command": "Get-NetTCPConnection", "parameters": parameters,
    })
    assert result.ok is False
    assert result.error["type"] == "PowerShellValidationError"


def test_run_powershell_rejects_invalid_tcp_state(workspace):
    result = execute_local_tool("run_powershell", {
        "command": "Get-NetTCPConnection", "parameters": {"State": "DefinitelyNotAState"},
    })
    assert result.ok is False
    assert result.error["type"] == "PowerShellValidationError"


def test_run_powershell_rejects_invalid_address_family(workspace):
    result = execute_local_tool("run_powershell", {
        "command": "Get-DnsClientServerAddress",
        "parameters": {"AddressFamily": "IPX"},
    })
    assert result.ok is False
    assert result.error["type"] == "PowerShellValidationError"


def test_run_powershell_get_net_route_is_structured(workspace):
    result = local_tools.run_powershell(
        "Get-NetRoute", {"AddressFamily": "IPv4"}
    )
    assert result["status"] == "completed"
    assert result["data_truncated"] is False
    assert result["data"]
    assert set(result["data"][0]) == {
        "InterfaceIndex", "AddressFamily", "DestinationPrefix",
        "NextHop", "RouteMetric", "State",
    }
    assert all(row["AddressFamily"] == "IPv4" for row in result["data"])


def test_run_powershell_get_net_ip_interface_is_structured(workspace):
    result = local_tools.run_powershell(
        "Get-NetIPInterface", {"AddressFamily": "IPv4"}
    )
    assert result["status"] == "completed"
    assert result["data_truncated"] is False
    assert result["data"]
    assert set(result["data"][0]) == {
        "InterfaceAlias", "InterfaceIndex", "AddressFamily",
        "ConnectionState", "Dhcp", "AutomaticMetric",
        "InterfaceMetric", "NlMtu",
    }
    assert all(row["AddressFamily"] == "IPv4" for row in result["data"])


def test_run_powershell_get_win_event_is_bounded_and_structured(workspace):
    result = local_tools.run_powershell(
        "Get-WinEvent", {"LogName": "Application", "MaxEvents": 2}
    )
    assert result["status"] == "completed"
    assert result["data_truncated"] is False
    assert 0 < len(result["data"]) <= 2
    assert set(result["data"][0]) == {
        "Id", "ProviderName", "LevelDisplayName", "TimeCreated", "Message",
    }
    assert all(len(row["Message"]) <= 1000 for row in result["data"])


@pytest.mark.parametrize("parameters", [
    {"LogName": "Security", "MaxEvents": 1},
    {"LogName": "Application", "MaxEvents": 51},
    {"LogName": "System", "MaxEvents": 0},
])
def test_run_powershell_rejects_unreviewed_win_event_requests(workspace, parameters):
    result = execute_local_tool("run_powershell", {
        "command": "Get-WinEvent", "parameters": parameters,
    })
    assert result.ok is False
    assert result.error["type"] == "PowerShellValidationError"


def test_run_powershell_get_scheduled_task_is_structured(workspace):
    result = local_tools.run_powershell(
        "Get-ScheduledTask", {"TaskName": "ScheduledDefrag"}
    )
    assert result["status"] == "completed"
    assert result["data_truncated"] is False
    assert result["data"]
    assert set(result["data"][0]) == {"TaskName", "TaskPath", "State"}
    assert result["data"][0]["TaskName"] == "ScheduledDefrag"


def test_run_powershell_get_authenticode_signature_is_workspace_bounded(workspace):
    target = workspace / "sample.ps1"
    target.write_text("Write-Output 'ok'\n", encoding="utf-8")
    result = local_tools.run_powershell(
        "Get-AuthenticodeSignature", {"FilePath": "sample.ps1"}
    )
    assert result["status"] == "completed"
    assert result["data_truncated"] is False
    assert result["data"]
    assert set(result["data"][0]) == {
        "Path", "Status", "StatusMessage",
        "SignerSubject", "SignerIssuer", "SignerThumbprint",
    }
    assert result["data"][0]["Path"].endswith("sample.ps1")


def test_run_powershell_get_acl_is_workspace_bounded(workspace):
    target = workspace / "acl.txt"
    target.write_text("ok", encoding="utf-8")
    result = local_tools.run_powershell("Get-Acl", {"LiteralPath": "acl.txt"})
    assert result["status"] == "completed"
    assert result["data_truncated"] is False
    assert result["data"]
    assert set(result["data"][0]) == {"Path", "Owner", "Access"}
    assert isinstance(result["data"][0]["Access"], list)


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
