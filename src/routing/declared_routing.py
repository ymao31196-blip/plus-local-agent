"""Compile and resolve declarative capability-routing metadata."""
from __future__ import annotations

from typing import Any


_AUTHORITY_RANK = {
    "recommendation": 0,
    "preferred": 1,
    "enforced": 2,
}

_RELATION_RANK = {
    "fallback_for": 1,
    "preferred_over": 1,
    "supersedes": 2,
}

_MODE_BY_RANK = {
    0: "specialized_recommended",
    1: "specialized_preferred",
    2: "specialized_enforced",
}


def _condition_matches(
    condition: dict[str, Any] | None,
    arguments: dict[str, Any],
) -> bool:
    if not condition:
        return True

    argument_name = condition.get("argument")
    if not isinstance(argument_name, str):
        return False
    value = arguments.get(argument_name)
    if not isinstance(value, str):
        return False
    normalized = value.casefold()

    contains_any = condition.get("contains_any")
    if isinstance(contains_any, list):
        return any(
            isinstance(item, str)
            and item.casefold() in normalized
            for item in contains_any
        )

    equals_any = condition.get("equals_any")
    if isinstance(equals_any, list):
        return any(
            isinstance(item, str)
            and item.casefold() == normalized
            for item in equals_any
        )
    return False


def collect_declared_relations(
    registry_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return normalized relations declared by registered capabilities."""
    capabilities = registry_snapshot.get("capabilities", [])
    if not isinstance(capabilities, list):
        raise TypeError("registry snapshot capabilities must be a list")

    relations: list[dict[str, Any]] = []
    for target in capabilities:
        if not isinstance(target, dict):
            continue
        target_id = target.get("id")
        provider_id = target.get("provider_id")
        routing = target.get("routing")
        authority = target.get("routing_authority", "recommendation")
        if (
            not isinstance(target_id, str)
            or not isinstance(provider_id, str)
            or not isinstance(routing, dict)
            or authority not in _AUTHORITY_RANK
        ):
            continue

        for relation_type, requested_rank in _RELATION_RANK.items():
            raw_relations = routing.get(relation_type, [])
            if not isinstance(raw_relations, list):
                continue
            for relation in raw_relations:
                if not isinstance(relation, dict):
                    continue
                source_id = relation.get("capability_id")
                if not isinstance(source_id, str):
                    continue
                effective_rank = min(
                    requested_rank,
                    _AUTHORITY_RANK[authority],
                )
                relations.append(
                    {
                        "source_capability_id": source_id,
                        "target_capability_id": target_id,
                        "target_provider_id": provider_id,
                        "relation_type": relation_type,
                        "routing_authority": authority,
                        "requested_mode": _MODE_BY_RANK[requested_rank],
                        "effective_mode": _MODE_BY_RANK[effective_rank],
                        "when": relation.get("when"),
                        "target_available": bool(target.get("available", False)),
                    }
                )

    relations.sort(
        key=lambda item: (
            -_AUTHORITY_RANK[item["routing_authority"]],
            -_RELATION_RANK[item["relation_type"]],
            item["source_capability_id"],
            item["target_capability_id"],
        )
    )
    return relations


def resolve_declared_route(
    capability_id: str,
    arguments: dict[str, Any],
    registry_snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve one capability invocation against declarative routing metadata."""
    capabilities = registry_snapshot.get("capabilities", [])
    if not isinstance(capabilities, list):
        raise TypeError("registry snapshot capabilities must be a list")

    availability = {
        str(item.get("id")): bool(item.get("available", False))
        for item in capabilities
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    source_available = availability.get(capability_id, False)

    matches: list[dict[str, Any]] = []
    for relation in collect_declared_relations(registry_snapshot):
        if relation["source_capability_id"] != capability_id:
            continue
        if not relation["target_available"]:
            continue
        if (
            relation["relation_type"] == "fallback_for"
            and source_available
        ):
            continue
        condition = relation.get("when")
        if condition is not None and not isinstance(condition, dict):
            continue
        if not _condition_matches(condition, arguments):
            continue
        matches.append(relation)

    if not matches:
        return None

    mode_rank = {
        "specialized_recommended": 0,
        "specialized_preferred": 1,
        "specialized_enforced": 2,
    }
    matches.sort(
        key=lambda item: (
            -mode_rank[item["effective_mode"]],
            item["target_capability_id"],
        )
    )
    selected = matches[0]
    mode = selected["effective_mode"]
    reason_by_mode = {
        "specialized_recommended": "specialized_capability_recommended",
        "specialized_preferred": "specialized_capability_preferred",
        "specialized_enforced": "specialized_capability_required",
    }

    return {
        "status": "blocked" if mode == "specialized_enforced" else "advisory",
        "reason": reason_by_mode[mode],
        "domain": "declared_capability_routing",
        "routing_mode": mode,
        "declared_relation": selected,
        "attempted_route": {
            "tool": "capability_invoke",
            "capability_id": capability_id,
            "argument_keys": sorted(str(key) for key in arguments),
        },
        "suggested_capabilities": [
            {
                "name": selected["target_capability_id"],
                "surface": "capability",
                "purpose": (
                    "Use the capability selected by declarative provider routing "
                    "metadata for this invocation context."
                ),
            }
        ],
        "message": (
            f"Declarative routing from {selected['target_provider_id']} "
            f"{selected['relation_type'].replace('_', ' ')} "
            f"{capability_id} with authority "
            f"{selected['routing_authority']}."
        ),
    }
