"""Versioned semantic execution policy for generic process governance.

The policy is deterministic and side-effect free. It consumes the command semantic
classification and decides whether a generic process must move to a confirmation-gated
Runtime capability. More-specific domain capabilities (for example governed Git commit
or push) remain higher-priority steering surfaces and are evaluated before this policy.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from execution.command_semantics import CommandSemantic


POLICY_VERSION = "1"
SPECIALIZED_GIT_ACTIONS = frozenset({"add", "rm", "commit", "tag", "push"})
HIGH_RISK_LOCAL_GIT_ACTIONS = frozenset(
    {
        "checkout",
        "switch",
        "reset",
        "clean",
        "rebase",
        "merge",
        "cherry-pick",
        "revert",
    }
)


@dataclass(frozen=True)
class SemanticExecutionDecision:
    policy_id: str
    policy_version: str
    target_capability: str
    reason: str
    domain: str
    confirmation_required: bool
    purpose: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_semantic_execution(semantic: CommandSemantic) -> SemanticExecutionDecision | None:
    """Return the confirmation policy for one semantic classification, if any."""

    if not isinstance(semantic, CommandSemantic):
        raise TypeError("semantic must be a CommandSemantic")

    if semantic.risk_level == "write_external":
        return SemanticExecutionDecision(
            policy_id="semantic_external_write",
            policy_version=POLICY_VERSION,
            target_capability="runtime.external_process",
            reason="external_write_confirmation_required",
            domain="external_write",
            confirmation_required=True,
            purpose="Run one semantic external-write command behind explicit INVOKE confirmation.",
        )

    if (
        semantic.risk_level == "write_local"
        and semantic.effect_class == "environment_change"
    ):
        return SemanticExecutionDecision(
            policy_id="environment_change",
            policy_version=POLICY_VERSION,
            target_capability="runtime.environment_process",
            reason="environment_change_confirmation_required",
            domain="environment_change",
            confirmation_required=True,
            purpose="Run one semantic environment-changing command behind explicit INVOKE confirmation.",
        )

    if (
        semantic.risk_level == "write_local"
        and semantic.domain == "git"
        and semantic.action in HIGH_RISK_LOCAL_GIT_ACTIONS
    ):
        return SemanticExecutionDecision(
            policy_id="high_risk_local_git",
            policy_version=POLICY_VERSION,
            target_capability="runtime.local_mutation_process",
            reason="local_mutation_confirmation_required",
            domain="git",
            confirmation_required=True,
            purpose="Run one governed high-risk local Git mutation behind explicit INVOKE confirmation.",
        )

    return None


def policy_catalog() -> list[dict[str, Any]]:
    """Return stable non-executable metadata for semantic confirmation policies."""

    return [
        {
            "policy_id": "semantic_external_write",
            "policy_version": POLICY_VERSION,
            "target_capability": "runtime.external_process",
            "match": {"risk_level": "write_external"},
        },
        {
            "policy_id": "environment_change",
            "policy_version": POLICY_VERSION,
            "target_capability": "runtime.environment_process",
            "match": {
                "risk_level": "write_local",
                "effect_class": "environment_change",
            },
        },
        {
            "policy_id": "high_risk_local_git",
            "policy_version": POLICY_VERSION,
            "target_capability": "runtime.local_mutation_process",
            "match": {
                "risk_level": "write_local",
                "domain": "git",
                "actions": sorted(HIGH_RISK_LOCAL_GIT_ACTIONS),
            },
        },
    ]
