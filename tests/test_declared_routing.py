from declared_routing import (
    collect_declared_relations,
    resolve_declared_route,
)


def _capability(
    capability_id,
    provider_id,
    *,
    available=True,
    authority="recommendation",
    routing=None,
):
    return {
        "id": capability_id,
        "provider_id": provider_id,
        "available": available,
        "routing_authority": authority,
        "routing": routing or {},
    }


def _snapshot(*capabilities):
    return {
        "capability_count": len(capabilities),
        "provider_count": len({item["provider_id"] for item in capabilities}),
        "capabilities": list(capabilities),
    }


def test_preferred_declaration_routes_when_condition_matches():
    snapshot = _snapshot(
        _capability("computer.click", "computer"),
        _capability(
            "browser.click",
            "browser",
            authority="preferred",
            routing={
                "preferred_over": [
                    {
                        "capability_id": "computer.click",
                        "when": {
                            "argument": "app",
                            "contains_any": ["edge", "chrome"],
                        },
                    }
                ]
            },
        ),
    )

    result = resolve_declared_route(
        "computer.click",
        {"app": "Microsoft Edge", "selector": "Save"},
        snapshot,
    )

    assert result["routing_mode"] == "specialized_preferred"
    assert result["suggested_capabilities"][0]["name"] == "browser.click"
    assert result["declared_relation"]["routing_authority"] == "preferred"


def test_recommendation_authority_caps_preferred_declaration():
    snapshot = _snapshot(
        _capability("source.action", "source"),
        _capability(
            "target.action",
            "target",
            authority="recommendation",
            routing={
                "preferred_over": [
                    {"capability_id": "source.action"}
                ]
            },
        ),
    )

    result = resolve_declared_route("source.action", {}, snapshot)

    assert result["routing_mode"] == "specialized_recommended"
    assert result["reason"] == "specialized_capability_recommended"


def test_enforced_authority_can_express_supersedes_for_builtin_future_use():
    snapshot = _snapshot(
        _capability("source.write", "source"),
        _capability(
            "target.write",
            "target",
            authority="enforced",
            routing={
                "supersedes": [
                    {"capability_id": "source.write"}
                ]
            },
        ),
    )

    result = resolve_declared_route("source.write", {}, snapshot)

    assert result["routing_mode"] == "specialized_enforced"
    assert result["status"] == "blocked"


def test_preferred_authority_caps_supersedes_to_preferred():
    snapshot = _snapshot(
        _capability("source.write", "source"),
        _capability(
            "target.write",
            "target",
            authority="preferred",
            routing={
                "supersedes": [
                    {"capability_id": "source.write"}
                ]
            },
        ),
    )

    result = resolve_declared_route("source.write", {}, snapshot)

    assert result["routing_mode"] == "specialized_preferred"
    assert result["status"] == "advisory"
    assert result["declared_relation"]["requested_mode"] == (
        "specialized_enforced"
    )
    assert result["declared_relation"]["effective_mode"] == (
        "specialized_preferred"
    )


def test_condition_is_case_insensitive_and_nonmatch_returns_none():
    snapshot = _snapshot(
        _capability("computer.click", "computer"),
        _capability(
            "browser.click",
            "browser",
            authority="preferred",
            routing={
                "preferred_over": [
                    {
                        "capability_id": "computer.click",
                        "when": {
                            "argument": "app",
                            "equals_any": ["microsoft edge"],
                        },
                    }
                ]
            },
        ),
    )

    assert resolve_declared_route(
        "computer.click",
        {"app": "MICROSOFT EDGE"},
        snapshot,
    )["routing_mode"] == "specialized_preferred"
    assert resolve_declared_route(
        "computer.click",
        {"app": "Notepad"},
        snapshot,
    ) is None


def test_unavailable_target_is_not_suggested():
    snapshot = _snapshot(
        _capability("source.action", "source"),
        _capability(
            "target.action",
            "target",
            available=False,
            authority="preferred",
            routing={
                "preferred_over": [
                    {"capability_id": "source.action"}
                ]
            },
        ),
    )

    assert resolve_declared_route("source.action", {}, snapshot) is None


def test_fallback_is_only_selected_when_source_is_unavailable():
    available_source = _snapshot(
        _capability("primary.read", "primary", available=True),
        _capability(
            "fallback.read",
            "fallback",
            authority="preferred",
            routing={
                "fallback_for": [
                    {"capability_id": "primary.read"}
                ]
            },
        ),
    )
    unavailable_source = _snapshot(
        _capability("primary.read", "primary", available=False),
        _capability(
            "fallback.read",
            "fallback",
            authority="preferred",
            routing={
                "fallback_for": [
                    {"capability_id": "primary.read"}
                ]
            },
        ),
    )

    assert resolve_declared_route("primary.read", {}, available_source) is None
    result = resolve_declared_route("primary.read", {}, unavailable_source)
    assert result["routing_mode"] == "specialized_preferred"
    assert result["suggested_capabilities"][0]["name"] == "fallback.read"


def test_collect_declared_relations_is_deterministic():
    snapshot = _snapshot(
        _capability("source.action", "source"),
        _capability(
            "target.action",
            "target",
            authority="preferred",
            routing={
                "preferred_over": [
                    {"capability_id": "source.action"}
                ]
            },
        ),
    )

    relations = collect_declared_relations(snapshot)

    assert relations == [
        {
            "source_capability_id": "source.action",
            "target_capability_id": "target.action",
            "target_provider_id": "target",
            "relation_type": "preferred_over",
            "routing_authority": "preferred",
            "requested_mode": "specialized_preferred",
            "effective_mode": "specialized_preferred",
            "when": None,
            "target_available": True,
        }
    ]
