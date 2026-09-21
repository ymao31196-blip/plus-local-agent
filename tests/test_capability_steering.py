import capability_steering as steering


def _browser_snapshot(
    source="computer.click",
    target="browser.click",
):
    return {
        "capability_count": 2,
        "provider_count": 2,
        "capabilities": [
            {
                "id": source,
                "provider_id": "computer",
                "available": True,
                "routing_authority": "recommendation",
                "routing": {},
            },
            {
                "id": target,
                "provider_id": "browser",
                "available": True,
                "routing_authority": "preferred",
                "routing": {
                    "preferred_over": [
                        {
                            "capability_id": source,
                            "when": {
                                "argument": "app",
                                "contains_any": [
                                    "edge",
                                    "chrome",
                                    "chromium",
                                    "firefox",
                                ],
                            },
                        }
                    ]
                },
            },
        ],
    }


def test_registry_describes_registered_rules_in_priority_order():
    rules = steering.steering_rules()

    assert {item["id"] for item in rules} == {
        "pla_source_git",
        "windows_service_write",
    }
    modes = {item["id"]: item["routing_mode"] for item in rules}
    assert modes["pla_source_git"] == "specialized_enforced"
    assert modes["windows_service_write"] == "specialized_enforced"


def test_pla_git_status_is_specialized_enforced():
    result = steering.steer_run_process("git.exe", ["status"], "pla")

    assert result["status"] == "blocked"
    assert result["reason"] == "specialized_capability_required"
    assert result["domain"] == "git"
    assert result["routing_mode"] == "specialized_enforced"
    assert result["steering_rule_id"] == "pla_source_git"
    assert result["attempted_route"]["subcommand"] == "status"
    assert result["suggested_capabilities"][0]["name"] == "git_status"


def test_pla_git_push_prefers_release_push_capability():
    result = steering.steer_run_process("git", ["push", "origin", "master"], "pla")

    assert result["suggested_capabilities"][0]["name"] == "core.git_push"
    assert result["suggested_capabilities"][0]["surface"] == "capability"


def test_pla_git_unknown_subcommand_still_returns_governed_surface():
    result = steering.steer_run_process("git", ["rev-parse", "HEAD"], "pla")

    assert result["attempted_route"]["subcommand"] is None
    assert {item["name"] for item in result["suggested_capabilities"]} == {
        "git_status",
        "git_diff",
        "git_stage",
        "git_commit",
        "core.git_tag",
        "core.git_push",
    }


def test_user_workspace_git_is_not_steered():
    assert steering.steer_run_process("git", ["status"], "workspace") is None


def test_gh_is_not_steered_by_git_rule():
    assert steering.steer_run_process("gh.exe", ["release", "list"], "pla") is None


def test_windows_service_restart_is_specialized_enforced():
    result = steering.steer_run_powershell(
        "Restart-Service",
        {"Name": "Spooler"},
        "workspace",
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "specialized_capability_required"
    assert result["domain"] == "windows_service"
    assert result["routing_mode"] == "specialized_enforced"
    assert result["steering_rule_id"] == "windows_service_write"
    assert result["attempted_route"]["operation"] == "restart"
    assert result["attempted_route"]["service_name"] == "Spooler"
    assert result["suggested_capabilities"][0]["name"] == (
        "windows.service_control_preflight"
    )
    assert result["suggested_capabilities"][1]["name"] == "windows.service_control"
    assert result["suggested_capabilities"][2]["name"] == (
        "windows.service_control_status"
    )


def test_windows_service_commands_are_case_insensitive():
    result = steering.steer_run_powershell(
        "stop-service",
        {"Name": "ExampleSvc"},
        "pla",
    )

    assert result["attempted_route"]["operation"] == "stop"
    assert result["attempted_route"]["service_name"] == "ExampleSvc"


def test_read_only_get_service_is_not_steered():
    assert steering.steer_run_powershell(
        "Get-Service",
        {"Name": "EventLog"},
        "workspace",
    ) is None


def test_route_generic_request_reports_specialized_git_route():
    result = steering.route_generic_request(
        "run_process",
        {
            "program": "git.exe",
            "args": ["commit", "-m", "x"],
            "root": "pla",
        },
    )

    assert result["status"] == "specialized_required"
    assert result["routing_mode"] == "specialized_enforced"
    assert result["steering_rule_id"] == "pla_source_git"
    assert result["suggested_capabilities"][0]["name"] == "git_commit"


def test_route_generic_request_reports_generic_allowed_for_gh():
    result = steering.route_generic_request(
        "run_process",
        {
            "program": "gh.exe",
            "args": ["release", "list"],
            "root": "pla",
        },
    )

    assert result["status"] == "generic_allowed"
    assert result["routing_mode"] == "generic_allowed"


def test_route_generic_request_reports_windows_service_route():
    result = steering.route_generic_request(
        "run_powershell",
        {
            "command": "Start-Service",
            "parameters": {"Name": "ExampleSvc"},
        },
    )

    assert result["status"] == "specialized_required"
    assert result["domain"] == "windows_service"
    assert result["attempted_route"]["operation"] == "start"
    assert result["suggested_capabilities"][0]["name"] == (
        "windows.service_control_preflight"
    )


def test_route_capability_invoke_prefers_browser_for_explicit_browser_app():
    result = steering.route_generic_request(
        "capability_invoke",
        {
            "capability_id": "computer.click",
            "arguments": {
                "app": "Microsoft Edge",
                "selector": "Save",
            },
        },
        _browser_snapshot(),
    )

    assert result["status"] == "specialized_preferred"
    assert result["routing_mode"] == "specialized_preferred"
    assert result["domain"] == "declared_capability_routing"
    assert result["declared_relation"]["target_capability_id"] == "browser.click"
    assert result["suggested_capabilities"][0]["name"] == "browser.click"


def test_route_capability_invoke_keeps_computer_for_non_browser_app():
    result = steering.route_generic_request(
        "capability_invoke",
        {
            "capability_id": "computer.click",
            "arguments": {
                "app": "Notepad",
                "selector": "Edit",
            },
        },
        _browser_snapshot(),
    )

    assert result["status"] == "generic_allowed"
    assert result["routing_mode"] == "generic_allowed"


def test_route_capability_invoke_does_not_guess_from_hwnd_only():
    result = steering.route_generic_request(
        "capability_invoke",
        {
            "capability_id": "computer.screenshot",
            "arguments": {"hwnd": 1234},
        },
        _browser_snapshot(
            "computer.screenshot",
            "browser.screenshot",
        ),
    )

    assert result["status"] == "generic_allowed"


def test_route_generic_request_rejects_unknown_generic_tool():
    try:
        steering.route_generic_request("shell", {})
    except ValueError as exc:
        assert "capability_invoke" in str(exc)
    else:
        raise AssertionError("Unknown generic tool route should be rejected")
