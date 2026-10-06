"""Conservative promotion/canary policy for the out-of-process Execution Runner.

Phase Q intentionally promotes only commands that are safe to repeat after an
ambiguous transport failure.  This avoids duplicate side effects if the Runner
executes a request but the Control Plane loses the response and falls back.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from command_semantics import CommandSemantic
from shadow_execution_policy import evaluate_shadow_execution


POLICY_VERSION = "1"


@dataclass(frozen=True)
class PromotionDecision:
    promoted: bool
    policy_id: str
    policy_version: str
    reason: str
    fallback_safe: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_candidate_promotion(
    semantic: CommandSemantic,
    *,
    command: Sequence[str],
    selected_root: str | None,
    env_overrides: Mapping[str, str] | None,
    stdin: str | bytes | None,
    text: bool,
) -> PromotionDecision:
    """Return whether one command may safely prefer the candidate Runner.

    Promotion v1 deliberately mirrors the strict Phase-P shadow eligibility set.
    Every promoted command is read-only, local, deterministic enough for parity
    comparison, and safe to repeat if a transport failure makes completion
    ambiguous.
    """

    shadow = evaluate_shadow_execution(
        semantic,
        command=command,
        selected_root=selected_root,
        env_overrides=env_overrides,
        stdin=stdin,
        text=text,
    )
    if not shadow.eligible:
        return PromotionDecision(
            promoted=False,
            policy_id="candidate_canary_readonly_git",
            policy_version=POLICY_VERSION,
            reason=shadow.reason,
            fallback_safe=False,
        )
    return PromotionDecision(
        promoted=True,
        policy_id="candidate_canary_readonly_git",
        policy_version=POLICY_VERSION,
        reason="shadow_verified_repeatable_readonly_git",
        fallback_safe=True,
    )
