"""Deterministic command semantic classification for PLA execution routes.

The classifier is descriptive and side-effect free. It does not execute commands,
grant permissions, or bypass capability steering. Its output is consumed by routing
and execution-observation layers so governance can reason about an action rather
than only an executable name.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal


RiskLevel = Literal["read", "write_local", "write_external", "privileged", "destructive"]
EffectClass = Literal[
    "read_only",
    "local_state_change",
    "external_state_change",
    "environment_change",
    "arbitrary_code",
    "bridge_execution",
    "unknown",
]
NetworkIntent = Literal["none", "read", "write", "possible"]
Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class CommandSemantic:
    program: str
    domain: str
    action: str
    risk_level: RiskLevel
    effect_class: EffectClass
    network_intent: NetworkIntent
    confidence: Confidence
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        return value


def _program_name(program: str) -> str:
    return Path(program).name.casefold()


def _result(
    *,
    program: str,
    domain: str,
    action: str,
    risk_level: RiskLevel,
    effect_class: EffectClass,
    network_intent: NetworkIntent = "none",
    confidence: Confidence = "high",
    reasons: tuple[str, ...] = (),
) -> CommandSemantic:
    return CommandSemantic(
        program=_program_name(program),
        domain=domain,
        action=action,
        risk_level=risk_level,
        effect_class=effect_class,
        network_intent=network_intent,
        confidence=confidence,
        reasons=reasons,
    )


_GIT_GLOBAL_OPTIONS_WITH_VALUE = {
    "-c",
    "-C",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--super-prefix",
    "--config-env",
}
_GIT_READ = {
    "status", "diff", "log", "show", "rev-parse", "ls-files", "ls-tree",
    "cat-file", "grep", "blame", "shortlog", "describe", "name-rev",
    "for-each-ref", "count-objects", "fsck", "archive", "version", "help",
}
_GIT_LOCAL_WRITE = {
    "add", "rm", "mv", "commit", "merge", "rebase", "cherry-pick", "revert",
    "reset", "restore", "checkout", "switch", "branch", "tag", "stash", "clean",
    "init", "worktree", "notes", "gc", "maintenance",
}
_GIT_NETWORK_READ_LOCAL_WRITE = {"fetch", "pull", "clone", "submodule"}


def _git_subcommand(args: list[str]) -> str | None:
    index = 0
    while index < len(args):
        token = args[index]
        folded = token.casefold()
        if token in _GIT_GLOBAL_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if any(folded.startswith(prefix + "=") for prefix in (
            "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"
        )):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return folded
    return None


def _classify_git(program: str, args: list[str]) -> CommandSemantic:
    action = _git_subcommand(args)
    if action is None:
        return _result(
            program=program,
            domain="git",
            action="unknown",
            risk_level="write_local",
            effect_class="unknown",
            confidence="low",
            reasons=("git subcommand could not be resolved",),
        )
    if action in _GIT_READ:
        return _result(
            program=program,
            domain="git",
            action=action,
            risk_level="read",
            effect_class="read_only",
        )
    if action == "ls-remote":
        return _result(
            program=program,
            domain="git",
            action=action,
            risk_level="read",
            effect_class="read_only",
            network_intent="read",
        )
    if action == "push":
        return _result(
            program=program,
            domain="git",
            action=action,
            risk_level="write_external",
            effect_class="external_state_change",
            network_intent="write",
        )
    if action in _GIT_NETWORK_READ_LOCAL_WRITE:
        return _result(
            program=program,
            domain="git",
            action=action,
            risk_level="write_local",
            effect_class="local_state_change",
            network_intent="read",
        )
    if action == "remote":
        lowered = [item.casefold() for item in args]
        read_only = any(item in {"-v", "--verbose", "get-url", "show"} for item in lowered)
        return _result(
            program=program,
            domain="git",
            action=action,
            risk_level="read" if read_only else "write_local",
            effect_class="read_only" if read_only else "local_state_change",
            network_intent="none",
            confidence="medium",
            reasons=("git remote semantics depend on nested arguments",),
        )
    if action == "config":
        lowered = [item.casefold() for item in args]
        read_only = any(item in {"--get", "--get-all", "--get-regexp", "--list", "-l"} for item in lowered)
        return _result(
            program=program,
            domain="git",
            action=action,
            risk_level="read" if read_only else "write_local",
            effect_class="read_only" if read_only else "local_state_change",
            confidence="medium",
            reasons=("git config may read or mutate repository/user configuration",),
        )
    if action in _GIT_LOCAL_WRITE:
        return _result(
            program=program,
            domain="git",
            action=action,
            risk_level="write_local",
            effect_class="local_state_change",
        )
    return _result(
        program=program,
        domain="git",
        action=action,
        risk_level="write_local",
        effect_class="unknown",
        network_intent="possible",
        confidence="low",
        reasons=("unrecognized git subcommand is conservatively treated as potentially mutating",),
    )


_GH_READ_ACTIONS = {
    "auth.status",
    "issue.list", "issue.view", "issue.status",
    "pr.list", "pr.view", "pr.status", "pr.checks", "pr.diff",
    "release.list", "release.view",
    "repo.list", "repo.view",
    "run.list", "run.view", "run.watch",
    "workflow.list", "workflow.view",
}
_GH_WRITE_ACTIONS = {
    "auth.login", "auth.logout", "auth.refresh",
    "issue.create", "issue.edit", "issue.close", "issue.reopen", "issue.delete",
    "issue.comment", "issue.lock", "issue.unlock", "issue.pin", "issue.unpin",
    "pr.create", "pr.edit", "pr.merge", "pr.close", "pr.reopen", "pr.comment",
    "pr.review", "pr.ready",
    "release.create", "release.edit", "release.delete", "release.upload",
    "repo.create", "repo.delete", "repo.edit", "repo.archive", "repo.fork",
    "run.cancel", "run.rerun", "run.delete",
    "workflow.run", "workflow.enable", "workflow.disable",
    "secret.set", "secret.delete", "variable.set", "variable.delete",
}
_GH_OPTIONS_WITH_VALUE = {"-R", "--repo", "--hostname", "--config"}


def _gh_positional(args: list[str]) -> list[str]:
    values: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token in _GH_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if any(token.startswith(prefix + "=") for prefix in ("--repo", "--hostname", "--config")):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        values.append(token.casefold())
        index += 1
    return values


def _classify_gh(program: str, args: list[str]) -> CommandSemantic:
    positional = _gh_positional(args)
    if not positional:
        return _result(
            program=program,
            domain="github",
            action="unknown",
            risk_level="write_external",
            effect_class="unknown",
            network_intent="possible",
            confidence="low",
            reasons=("GitHub CLI action could not be resolved",),
        )
    if positional[0] == "api":
        method = "GET"
        for index, token in enumerate(args):
            if token in {"-X", "--method"} and index + 1 < len(args):
                method = args[index + 1].upper()
            elif token.startswith("--method="):
                method = token.split("=", 1)[1].upper()
        is_read = method in {"GET", "HEAD"}
        return _result(
            program=program,
            domain="github",
            action=f"api.{method.casefold()}",
            risk_level="read" if is_read else "write_external",
            effect_class="read_only" if is_read else "external_state_change",
            network_intent="read" if is_read else "write",
            confidence="medium",
            reasons=("gh api classification is based on HTTP method",),
        )
    action = positional[0]
    if len(positional) > 1:
        action = f"{action}.{positional[1]}"
    if action in _GH_READ_ACTIONS:
        return _result(
            program=program,
            domain="github",
            action=action,
            risk_level="read",
            effect_class="read_only",
            network_intent="read",
        )
    if action == "repo.clone":
        return _result(
            program=program,
            domain="github",
            action=action,
            risk_level="write_local",
            effect_class="local_state_change",
            network_intent="read",
        )
    if action in _GH_WRITE_ACTIONS:
        return _result(
            program=program,
            domain="github",
            action=action,
            risk_level="write_external",
            effect_class="external_state_change",
            network_intent="write",
        )
    return _result(
        program=program,
        domain="github",
        action=action,
        risk_level="write_external",
        effect_class="unknown",
        network_intent="possible",
        confidence="low",
        reasons=("unrecognized gh action is conservatively treated as potentially external-writing",),
    )


def _classify_python(program: str, args: list[str]) -> CommandSemantic:
    lowered = [item.casefold() for item in args]
    interactive = "-i" in lowered
    action = "interactive" if interactive else "script"
    if "-m" in lowered:
        index = lowered.index("-m")
        module = lowered[index + 1] if index + 1 < len(lowered) else ""
        action = f"module.{module or 'unknown'}"
        if module == "pip":
            pip_args = lowered[index + 2 :]
            pip_action = next(
                (item for item in pip_args if not item.startswith("-")),
                None,
            )
            if "--version" in pip_args or pip_action in {
                "list", "show", "freeze", "check", "debug", "help",
            }:
                return _result(
                    program=program,
                    domain="python",
                    action=f"module.pip.{pip_action or 'version'}",
                    risk_level="read",
                    effect_class="read_only",
                    network_intent="none",
                    confidence="high",
                    reasons=("pip query does not modify the Python environment",),
                )
            if pip_action == "index":
                return _result(
                    program=program,
                    domain="python",
                    action="module.pip.index",
                    risk_level="read",
                    effect_class="read_only",
                    network_intent="read",
                    confidence="high",
                    reasons=("pip index queries package metadata without installing it",),
                )
            if pip_action in {"install", "uninstall"}:
                return _result(
                    program=program,
                    domain="python",
                    action=f"module.pip.{pip_action}",
                    risk_level="write_local",
                    effect_class="environment_change",
                    network_intent="possible" if pip_action == "install" else "none",
                    confidence="high",
                    reasons=("pip action modifies the local Python environment",),
                )
            return _result(
                program=program,
                domain="python",
                action=f"module.pip.{pip_action or 'unknown'}",
                risk_level="write_local",
                effect_class="local_state_change",
                network_intent="possible",
                confidence="medium",
                reasons=("pip action may modify local cache, files, or configuration",),
            )
        if module in {"ensurepip", "venv"}:
            return _result(
                program=program,
                domain="python",
                action=action,
                risk_level="write_local",
                effect_class="environment_change",
                network_intent="possible" if module == "ensurepip" else "none",
                confidence="high",
                reasons=("Python module can modify the local runtime environment",),
            )
        if module == "pytest":
            return _result(
                program=program,
                domain="test",
                action="pytest",
                risk_level="write_local",
                effect_class="arbitrary_code",
                network_intent="possible",
                confidence="high",
                reasons=("pytest executes repository test code",),
            )
        if module == "code":
            return _result(
                program=program,
                domain="python",
                action="module.code",
                risk_level="write_local",
                effect_class="arbitrary_code",
                network_intent="possible",
                confidence="high",
                reasons=("python -m code starts an interactive Python console",),
            )
    if interactive:
        action = "interactive"
    elif "-c" in lowered:
        action = "inline_code"
    else:
        positional = [item for item in args if not item.startswith("-")]
        action = "script" if positional else "repl"
    return _result(
        program=program,
        domain="python",
        action=action,
        risk_level="write_local",
        effect_class="arbitrary_code",
        network_intent="possible",
        confidence="high",
        reasons=("Python can execute arbitrary user or repository code",),
    )


def classify_command(program: str, args: list[str] | None = None) -> CommandSemantic:
    if not isinstance(program, str) or not program:
        raise ValueError("program must be a non-empty string")
    args = [] if args is None else args
    if not isinstance(args, list) or any(not isinstance(item, str) for item in args):
        raise TypeError("args must be an array of strings")

    name = _program_name(program)
    if name in {"git", "git.exe"}:
        return _classify_git(program, args)
    if name in {"gh", "gh.exe"}:
        return _classify_gh(program, args)
    if name in {"python", "python.exe"}:
        return _classify_python(program, args)
    if name in {"pytest", "pytest.exe"}:
        return _result(
            program=program,
            domain="test",
            action="pytest",
            risk_level="write_local",
            effect_class="arbitrary_code",
            network_intent="possible",
            reasons=("pytest executes repository test code",),
        )
    if name in {"latexmk", "latexmk.exe", "xelatex", "xelatex.exe"}:
        return _result(
            program=program,
            domain="typesetting",
            action="build",
            risk_level="write_local",
            effect_class="local_state_change",
            network_intent="none",
            reasons=("typesetting writes build artifacts under the selected workspace",),
        )
    if name in {"wsl", "wsl.exe"}:
        return _result(
            program=program,
            domain="wsl",
            action="bridge_execution",
            risk_level="write_local",
            effect_class="bridge_execution",
            network_intent="possible",
            confidence="high",
            reasons=("WSL bridges into a separate execution environment",),
        )
    return _result(
        program=program,
        domain="generic",
        action="unknown",
        risk_level="write_local",
        effect_class="unknown",
        network_intent="possible",
        confidence="low",
        reasons=("no semantic classifier is registered for this executable",),
    )
