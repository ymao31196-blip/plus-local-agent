from execution import command_semantics as semantics
def _classify(program, args):
    return semantics.classify_command(program, args).to_dict()


def test_git_read_and_write_actions_are_distinguished():
    status = _classify("git.exe", ["status", "--short"])
    assert status["domain"] == "git"
    assert status["action"] == "status"
    assert status["risk_level"] == "read"
    assert status["effect_class"] == "read_only"
    assert status["network_intent"] == "none"

    push = _classify("git", ["push", "origin", "master"])
    assert push["action"] == "push"
    assert push["risk_level"] == "write_external"
    assert push["effect_class"] == "external_state_change"
    assert push["network_intent"] == "write"


def test_git_global_options_do_not_hide_subcommand():
    result = _classify("git", ["-C", "repo", "-c", "core.fsmonitor=false", "diff", "--stat"])
    assert result["action"] == "diff"
    assert result["risk_level"] == "read"


def test_git_network_read_local_write_is_explicit():
    result = _classify("git", ["clone", "https://example.invalid/repo.git"])
    assert result["risk_level"] == "write_local"
    assert result["effect_class"] == "local_state_change"
    assert result["network_intent"] == "read"


def test_git_config_read_and_write_are_distinguished():
    read = _classify("git", ["config", "--get", "user.name"])
    write = _classify("git", ["config", "user.name", "Example"])
    assert read["risk_level"] == "read"
    assert write["risk_level"] == "write_local"


def test_unknown_git_is_conservative():
    result = _classify("git", ["future-command"])
    assert result["effect_class"] == "unknown"
    assert result["risk_level"] == "write_local"
    assert result["network_intent"] == "possible"
    assert result["confidence"] == "low"


def test_gh_read_write_and_clone_actions_are_distinguished():
    view = _classify("gh.exe", ["repo", "view", "openai/codex"])
    assert view["action"] == "repo.view"
    assert view["risk_level"] == "read"
    assert view["network_intent"] == "read"

    create = _classify("gh", ["issue", "create", "--title", "x"])
    assert create["action"] == "issue.create"
    assert create["risk_level"] == "write_external"
    assert create["network_intent"] == "write"

    clone = _classify("gh", ["repo", "clone", "openai/codex"])
    assert clone["action"] == "repo.clone"
    assert clone["risk_level"] == "write_local"
    assert clone["network_intent"] == "read"


def test_gh_api_uses_http_method_semantics():
    get = _classify("gh", ["api", "repos/openai/codex"])
    post = _classify("gh", ["api", "-X", "POST", "repos/openai/codex/issues"])
    assert get["action"] == "api.get"
    assert get["risk_level"] == "read"
    assert post["action"] == "api.post"
    assert post["risk_level"] == "write_external"


def test_python_environment_and_test_execution_are_distinguished():
    pip = _classify("python", ["-m", "pip", "install", "example"])
    assert pip["action"] == "module.pip.install"
    assert pip["effect_class"] == "environment_change"
    assert pip["network_intent"] == "possible"

    pip_list = _classify("python", ["-m", "pip", "list"])
    assert pip_list["action"] == "module.pip.list"
    assert pip_list["risk_level"] == "read"
    assert pip_list["effect_class"] == "read_only"

    pip_index = _classify("python", ["-m", "pip", "index", "versions", "example"])
    assert pip_index["risk_level"] == "read"
    assert pip_index["network_intent"] == "read"

    pytest = _classify("python.exe", ["-m", "pytest", "-q"])
    assert pytest["domain"] == "test"
    assert pytest["effect_class"] == "arbitrary_code"

    script = _classify("python", ["script.py"])
    assert script["domain"] == "python"
    assert script["action"] == "script"
    assert script["effect_class"] == "arbitrary_code"

    inline = _classify("python", ["-c", "print('x')"])
    assert inline["action"] == "inline_code"

    repl = _classify("python", ["-u"])
    assert repl["action"] == "repl"

    interactive = _classify("python", ["-i", "script.py"])
    assert interactive["action"] == "interactive"

    module_code = _classify("python", ["-m", "code"])
    assert module_code["action"] == "module.code"


def test_other_allowlisted_programs_have_explicit_semantics():
    test = _classify("pytest.exe", ["-q"])
    assert test["domain"] == "test"
    assert test["effect_class"] == "arbitrary_code"

    latex = _classify("latexmk", ["main.tex"])
    assert latex["domain"] == "typesetting"
    assert latex["effect_class"] == "local_state_change"

    wsl = _classify("wsl.exe", ["bash", "-lc", "echo hi"])
    assert wsl["domain"] == "wsl"
    assert wsl["effect_class"] == "bridge_execution"
    assert wsl["network_intent"] == "possible"
