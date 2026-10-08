"""Strict eligibility policy for opt-in candidate shadow execution.

Shadow execution is intentionally narrower than ordinary read-only command semantics.
A command may be semantically read-only yet still consult external helpers, network
resources, mutable configuration, or user-provided stdin.  This policy therefore
allows only a small set of local Git structural queries with stable argument shapes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from execution.command_semantics import CommandSemantic


POLICY_VERSION = "1"
_SAFE_GIT_ACTIONS = frozenset({
    "rev-parse",
    "ls-files",
    "ls-tree",
    "for-each-ref",
    "count-objects",
})
_FORBIDDEN_GIT_CONTEXT_OPTIONS = frozenset({
    "-c",
    "-C",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--super-prefix",
    "--config-env",
})
_FORBIDDEN_GIT_CONTEXT_PREFIXES = tuple(
    option + "="
    for option in (
        "--git-dir",
        "--work-tree",
        "--namespace",
        "--super-prefix",
        "--config-env",
    )
)


@dataclass(frozen=True)
class ShadowExecutionDecision:
    eligible: bool
    policy_id: str
    policy_version: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_shadow_execution(
    semantic: CommandSemantic,
    *,
    command: Sequence[str],
    selected_root: str | None,
    env_overrides: Mapping[str, str] | None,
    stdin: str | bytes | None,
    text: bool,
) -> ShadowExecutionDecision:
    """Return whether one already-governed command is safe to execute a second time."""

    base = {
        "policy_id": "candidate_shadow_readonly_git",
        "policy_version": POLICY_VERSION,
    }

    def reject(reason: str) -> ShadowExecutionDecision:
        return ShadowExecutionDecision(False, reason=reason, **base)

    if not text:
        return reject("binary_mode_not_shadowable")
    if stdin is not None:
        return reject("stdin_not_shadowable")
    if env_overrides:
        return reject("env_overrides_not_shadowable")
    if not selected_root or selected_root == "pla":
        return reject("pla_or_missing_root_not_shadowable")
    if semantic.domain != "git":
        return reject("domain_not_shadowable")
    if semantic.action not in _SAFE_GIT_ACTIONS:
        return reject("git_action_not_shadowable")
    if (
        semantic.risk_level != "read"
        or semantic.effect_class != "read_only"
        or semantic.network_intent != "none"
        or semantic.confidence != "high"
    ):
        return reject("semantic_not_strict_readonly")

    args = list(command[1:])
    for token in args:
        if token in _FORBIDDEN_GIT_CONTEXT_OPTIONS:
            return reject("git_context_override_not_shadowable")
        if token.startswith(_FORBIDDEN_GIT_CONTEXT_PREFIXES):
            return reject("git_context_override_not_shadowable")

    return ShadowExecutionDecision(
        True,
        policy_id=base["policy_id"],
        policy_version=base["policy_version"],
        reason="strict_local_readonly_git",
    )
