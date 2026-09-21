"""Read-only routing coverage audit for the PLA capability registry."""
from __future__ import annotations

import re
from typing import Any

from declared_routing import collect_declared_relations


_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)

_GENERIC_TAGS = {
    "mcp",
    "read",
    "write",
    "observation",
    "interaction",
    "fallback",
    "semantic",
    "status",
    "runtime",
    "system",
}

_ACTION_ALIASES = {
    "click": {"click"},
    "type": {"type", "typing"},
    "press": {"press", "key"},
    "wait": {"wait"},
    "screenshot": {"screenshot"},
    "inspect": {"inspect"},
    "search": {"search", "find"},
    "install": {"install"},
    "uninstall": {"uninstall"},
    "download": {"download"},
    "upload": {"upload"},
}

_SKIP_TOKENS = {
    "status",
    "preview",
    "assess",
    "assessment",
    "prepare",
    "verify",
    "verification",
    "diagnostics",
    "diagnostic",
}

_KNOWN_PROVIDER_RELATIONS = {
    frozenset({"browser", "computer"}): "browser_ui_fallback",
}

_MIN_CANDIDATE_SCORE = 7

_REVIEWED_PARALLEL = {
    frozenset({
        "winget.install-winget-package",
        "software-migration.execute_winget_install",
    }): {
        "review_id": "winget_install_vs_migration_install",
        "decision": "keep_parallel",
        "reason": (
            "The WinGet provider is the general package-install surface, while "
            "software-migration adds migration preconditions, transaction governance, "
            "target-directory support, and post-action verification."
        ),
    },
    frozenset({
        "winget.install-winget-package",
        "software-migration.execute_elevated_winget_install",
    }): {
        "review_id": "winget_install_vs_elevated_migration_install",
        "decision": "keep_parallel",
        "reason": (
            "The elevated migration path is specifically for a reviewed target "
            "directory through the Interactive Elevation Broker; it is not a "
            "drop-in replacement for ordinary WinGet installation."
        ),
    },
    frozenset({
        "windows-management.uninstall_software",
        "software-migration.execute_winget_uninstall",
    }): {
        "review_id": "generic_uninstall_vs_migration_uninstall",
        "decision": "keep_parallel",
        "reason": (
            "Windows Management exposes a general preview/execute uninstaller, while "
            "software-migration performs an exact package-oriented migration step "
            "with transaction governance and uninstall-registry verification."
        ),
    },
}


def _tokens(*values: str) -> set[str]:
    result: set[str] = set()
    for value in values:
        result.update(token.casefold() for token in _TOKEN_RE.findall(value))
    return result


def _action(capability: dict[str, Any]) -> str | None:
    tokens = _tokens(
        str(capability.get("id") or ""),
        str(capability.get("remote_name") or ""),
        str(capability.get("title") or ""),
    )
    if tokens & _SKIP_TOKENS:
        return None
    for action, aliases in _ACTION_ALIASES.items():
        if tokens & aliases:
            return action
    return None


def _meaningful_tags(
    capability: dict[str, Any],
    action: str,
) -> set[str]:
    tags = {
        str(item).casefold()
        for item in capability.get("tags", [])
        if isinstance(item, str) and item
    }
    tags -= _GENERIC_TAGS
    tags -= _ACTION_ALIASES.get(action, set())
    return tags


def _catalog_mapping_index(
    catalog: list[dict[str, Any]],
) -> dict[frozenset[str], str]:
    index: dict[frozenset[str], str] = {}
    for entry in catalog:
        rule_id = str(entry.get("id") or "")
        for mapping in entry.get("mappings", []):
            if not isinstance(mapping, dict):
                continue
            source = mapping.get("from")
            target = mapping.get("to")
            if isinstance(source, str) and isinstance(target, str):
                index[frozenset({source, target})] = rule_id
    return index


def _catalog_health(
    capability_ids: set[str],
    catalog: list[dict[str, Any]],
    executable_rules: list[dict[str, Any]],
) -> dict[str, Any]:
    catalog_ids = {
        str(item.get("id"))
        for item in catalog
        if isinstance(item, dict) and item.get("id")
    }
    executable_ids = {
        str(item.get("id"))
        for item in executable_rules
        if isinstance(item, dict) and item.get("id")
    }

    missing_capabilities: list[dict[str, str]] = []
    for entry in catalog:
        rule_id = str(entry.get("id") or "")
        for target in entry.get("targets", []):
            if not isinstance(target, dict):
                continue
            if target.get("surface") != "capability":
                continue
            name = target.get("name")
            if isinstance(name, str) and name not in capability_ids:
                missing_capabilities.append(
                    {"rule_id": rule_id, "capability_id": name}
                )
        for mapping in entry.get("mappings", []):
            if not isinstance(mapping, dict):
                continue
            for side in ("from", "to"):
                name = mapping.get(side)
                if isinstance(name, str) and name not in capability_ids:
                    missing_capabilities.append(
                        {
                            "rule_id": rule_id,
                            "capability_id": name,
                        }
                    )

    return {
        "healthy": (
            not missing_capabilities
            and catalog_ids == executable_ids
        ),
        "catalog_rule_ids": sorted(catalog_ids),
        "executable_rule_ids": sorted(executable_ids),
        "catalog_only_rule_ids": sorted(catalog_ids - executable_ids),
        "executable_only_rule_ids": sorted(executable_ids - catalog_ids),
        "missing_capabilities": sorted(
            missing_capabilities,
            key=lambda item: (
                item["rule_id"],
                item["capability_id"],
            ),
        ),
    }


def audit_routing(
    registry_snapshot: dict[str, Any],
    catalog: list[dict[str, Any]],
    executable_rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """Find conservative cross-provider overlap candidates without changing routing."""
    capabilities = registry_snapshot.get("capabilities", [])
    if not isinstance(capabilities, list):
        raise TypeError("registry snapshot capabilities must be a list")

    usable = [
        item
        for item in capabilities
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("provider_id"), str)
    ]
    capability_ids = {str(item["id"]) for item in usable}
    mapping_index = _catalog_mapping_index(catalog)
    declared_relations = collect_declared_relations(registry_snapshot)
    declaration_index: dict[frozenset[str], dict[str, Any]] = {}
    for declared in declared_relations:
        pair = frozenset({
            declared["source_capability_id"],
            declared["target_capability_id"],
        })
        declaration_index.setdefault(pair, declared)

    candidates: list[dict[str, Any]] = []
    for index, left in enumerate(usable):
        left_action = _action(left)
        if left_action is None:
            continue
        for right in usable[index + 1:]:
            if left["provider_id"] == right["provider_id"]:
                continue
            right_action = _action(right)
            if right_action != left_action:
                continue

            score = 4
            same_risk = left.get("risk_level") == right.get("risk_level")
            if same_risk:
                score += 2

            shared_tags = sorted(
                _meaningful_tags(left, left_action)
                & _meaningful_tags(right, right_action)
            )
            score += min(3, len(shared_tags))

            relation = _KNOWN_PROVIDER_RELATIONS.get(
                frozenset({
                    str(left["provider_id"]),
                    str(right["provider_id"]),
                })
            )
            if relation is not None:
                score += 3

            if left_action == "search" and relation is None:
                continue

            if score < _MIN_CANDIDATE_SCORE:
                continue

            pair = frozenset({str(left["id"]), str(right["id"])})
            covering_rule_id = mapping_index.get(pair)
            covering_declaration = declaration_index.get(pair)
            reviewed_parallel = _REVIEWED_PARALLEL.get(pair)
            if covering_rule_id is not None or covering_declaration is not None:
                coverage = "covered"
            elif reviewed_parallel is not None:
                coverage = "reviewed_parallel"
            else:
                coverage = "uncovered"

            candidates.append(
                {
                    "action": left_action,
                    "left": str(left["id"]),
                    "right": str(right["id"]),
                    "providers": [
                        str(left["provider_id"]),
                        str(right["provider_id"]),
                    ],
                    "risk_levels": [
                        str(left.get("risk_level") or ""),
                        str(right.get("risk_level") or ""),
                    ],
                    "same_risk": same_risk,
                    "shared_tags": shared_tags,
                    "provider_relation": relation,
                    "score": score,
                    "coverage": coverage,
                    "covering_rule_id": covering_rule_id,
                    "covering_declaration": (
                        dict(covering_declaration)
                        if covering_declaration is not None
                        else None
                    ),
                    "review": (
                        dict(reviewed_parallel)
                        if reviewed_parallel is not None
                        else None
                    ),
                }
            )

    coverage_order = {
        "uncovered": 0,
        "reviewed_parallel": 1,
        "covered": 2,
    }
    candidates.sort(
        key=lambda item: (
            coverage_order.get(item["coverage"], 99),
            -item["score"],
            item["action"],
            item["left"],
            item["right"],
        )
    )
    uncovered = [
        item for item in candidates
        if item["coverage"] == "uncovered"
    ]
    reviewed_parallel = [
        item for item in candidates
        if item["coverage"] == "reviewed_parallel"
    ]
    covered = [
        item for item in candidates
        if item["coverage"] == "covered"
    ]

    return {
        "registry": {
            "capability_count": registry_snapshot.get(
                "capability_count", len(usable)
            ),
            "provider_count": registry_snapshot.get("provider_count"),
        },
        "catalog": {
            "rule_count": len(catalog),
            "rules": catalog,
            "health": _catalog_health(
                capability_ids,
                catalog,
                executable_rules,
            ),
        },
        "declarations": {
            "relation_count": len(declared_relations),
            "relations": declared_relations,
        },
        "candidate_count": len(candidates),
        "covered_candidate_count": len(covered),
        "reviewed_parallel_candidate_count": len(reviewed_parallel),
        "uncovered_candidate_count": len(uncovered),
        "candidates": candidates,
        "notes": [
            "Candidates are review hints, not executable routing rules.",
            (
                "The audit uses conservative action aliases, risk compatibility, "
                "shared tags, and explicit provider relations."
            ),
            (
                "reviewed_parallel means the overlap was examined and intentionally "
                "kept as separate capability surfaces."
            ),
            (
                "Only uncovered candidates still require routing review."
            ),
        ],
    }
