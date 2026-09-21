from routing_audit import audit_routing


def _capability(
    capability_id,
    provider_id,
    remote_name,
    title,
    tags,
    risk_level,
    *,
    routing_authority="recommendation",
    routing=None,
):
    return {
        "id": capability_id,
        "provider_id": provider_id,
        "remote_name": remote_name,
        "title": title,
        "tags": tags,
        "risk_level": risk_level,
        "available": True,
        "routing_authority": routing_authority,
        "routing": routing or {},
    }


def test_audit_marks_catalog_mapping_covered_and_reviewed_overlap_parallel():
    snapshot = {
        "capability_count": 4,
        "provider_count": 4,
        "capabilities": [
            _capability(
                "browser.click",
                "browser",
                "browser_click",
                "Click Browser Element",
                ["mcp", "browser", "playwright", "interaction"],
                "write_external",
            ),
            _capability(
                "computer.click",
                "computer",
                "click",
                "Click Windows UI Element",
                ["mcp", "computer", "windows", "interaction", "fallback"],
                "write_external",
            ),
            _capability(
                "winget.install-winget-package",
                "winget",
                "install-winget-package",
                "Install WinGet Package",
                ["mcp", "windows", "winget", "package", "software", "install"],
                "privileged",
            ),
            _capability(
                "software-migration.execute_winget_install",
                "software-migration",
                "execute_winget_install",
                "Execute WinGet Install",
                ["mcp", "windows", "winget", "software", "migration", "install"],
                "privileged",
            ),
        ],
    }
    catalog = [
        {
            "id": "browser_over_computer",
            "targets": [],
            "mappings": [
                {"from": "computer.click", "to": "browser.click"},
            ],
        }
    ]
    rules = [{"id": "browser_over_computer"}]

    result = audit_routing(snapshot, catalog, rules)

    assert result["catalog"]["health"]["healthy"] is True
    assert result["candidate_count"] == 2
    assert result["covered_candidate_count"] == 1
    assert result["reviewed_parallel_candidate_count"] == 1
    assert result["uncovered_candidate_count"] == 0

    covered = next(
        item for item in result["candidates"]
        if item["coverage"] == "covered"
    )
    assert {covered["left"], covered["right"]} == {
        "browser.click",
        "computer.click",
    }
    assert covered["covering_rule_id"] == "browser_over_computer"

    reviewed = next(
        item for item in result["candidates"]
        if item["coverage"] == "reviewed_parallel"
    )
    assert {reviewed["left"], reviewed["right"]} == {
        "winget.install-winget-package",
        "software-migration.execute_winget_install",
    }
    assert reviewed["review"]["decision"] == "keep_parallel"
    assert reviewed["score"] >= 7


def test_audit_marks_declarative_relation_as_covered():
    snapshot = {
        "capability_count": 2,
        "provider_count": 2,
        "capabilities": [
            _capability(
                "computer.click",
                "computer",
                "click",
                "Click Windows UI Element",
                ["mcp", "computer", "windows", "interaction", "fallback"],
                "write_external",
            ),
            _capability(
                "browser.click",
                "browser",
                "browser_click",
                "Click Browser Element",
                ["mcp", "browser", "playwright", "interaction"],
                "write_external",
                routing_authority="preferred",
                routing={
                    "preferred_over": [
                        {
                            "capability_id": "computer.click",
                            "when": {
                                "argument": "app",
                                "contains_any": ["edge"],
                            },
                        }
                    ]
                },
            ),
        ],
    }

    result = audit_routing(snapshot, [], [])

    assert result["candidate_count"] == 1
    assert result["covered_candidate_count"] == 1
    assert result["uncovered_candidate_count"] == 0
    candidate = result["candidates"][0]
    assert candidate["coverage"] == "covered"
    assert candidate["covering_rule_id"] is None
    assert candidate["covering_declaration"]["source_capability_id"] == (
        "computer.click"
    )
    assert candidate["covering_declaration"]["target_capability_id"] == (
        "browser.click"
    )
    assert result["declarations"]["relation_count"] == 1


def test_audit_suppresses_unrelated_cross_provider_search_noise():
    snapshot = {
        "capability_count": 2,
        "provider_count": 2,
        "capabilities": [
            _capability(
                "computer.search",
                "computer",
                "search",
                "Search Windows UI",
                ["mcp", "computer", "windows", "uia", "search"],
                "read",
            ),
            _capability(
                "windows-management.search_process_by_name",
                "windows-management",
                "search_process_by_name",
                "Search Process By Name",
                ["mcp", "windows", "system", "observation"],
                "read",
            ),
        ],
    }

    result = audit_routing(snapshot, [], [])

    assert result["candidate_count"] == 0
    assert result["uncovered_candidate_count"] == 0


def test_audit_catalog_health_detects_rule_and_capability_drift():
    snapshot = {
        "capability_count": 1,
        "provider_count": 1,
        "capabilities": [
            _capability(
                "alpha.click",
                "alpha",
                "click",
                "Click",
                ["interaction"],
                "write_external",
            ),
        ],
    }
    catalog = [
        {
            "id": "stale_rule",
            "targets": [
                {
                    "name": "missing.capability",
                    "surface": "capability",
                }
            ],
            "mappings": [],
        }
    ]

    result = audit_routing(snapshot, catalog, [])

    health = result["catalog"]["health"]
    assert health["healthy"] is False
    assert health["catalog_only_rule_ids"] == ["stale_rule"]
    assert health["missing_capabilities"] == [
        {
            "rule_id": "stale_rule",
            "capability_id": "missing.capability",
        }
    ]
