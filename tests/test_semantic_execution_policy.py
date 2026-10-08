from execution import command_semantics as semantics
from routing import capability_steering as steering
from execution.semantic_execution_policy import (
    HIGH_RISK_LOCAL_GIT_ACTIONS,
    POLICY_VERSION,
    evaluate_semantic_execution,
    policy_catalog,
)


def _decision(program, args):
    semantic = semantics.classify_command(program, args)
    return semantic, evaluate_semantic_execution(semantic)


def test_external_environment_and_high_risk_git_share_versioned_policy():
    semantic, external = _decision("gh", ["issue", "create", "--title", "x"])
    assert semantic.risk_level == "write_external"
    assert external.policy_id == "semantic_external_write"
    assert external.policy_version == POLICY_VERSION
    assert external.target_capability == "runtime.external_process"
    assert external.confirmation_required is True

    semantic, environment = _decision("python", ["-m", "pip", "install", "example"])
    assert semantic.effect_class == "environment_change"
    assert environment.policy_id == "environment_change"
    assert environment.policy_version == POLICY_VERSION
    assert environment.target_capability == "runtime.environment_process"

    semantic, local_git = _decision("git", ["reset", "--hard", "HEAD"])
    assert semantic.action == "reset"
    assert local_git.policy_id == "high_risk_local_git"
    assert local_git.policy_version == POLICY_VERSION
    assert local_git.target_capability == "runtime.local_mutation_process"


def test_generic_read_and_ordinary_local_execution_have_no_confirmation_policy():
    _, git_status = _decision("git", ["status"])
    assert git_status is None

    _, python_inline = _decision("python", ["-c", "print('x')"])
    assert python_inline is None

    _, pip_list = _decision("python", ["-m", "pip", "list"])
    assert pip_list is None


def test_policy_catalog_is_stable_and_covers_high_risk_git_actions():
    catalog = {item["policy_id"]: item for item in policy_catalog()}
    assert set(catalog) == {
        "semantic_external_write",
        "environment_change",
        "high_risk_local_git",
    }
    assert catalog["semantic_external_write"]["policy_version"] == POLICY_VERSION
    assert catalog["environment_change"]["target_capability"] == (
        "runtime.environment_process"
    )
    assert set(catalog["high_risk_local_git"]["match"]["actions"]) == set(
        HIGH_RISK_LOCAL_GIT_ACTIONS
    )


def test_steering_exposes_same_semantic_policy_decision():
    cases = [
        (
            "gh",
            ["issue", "create", "--title", "x"],
            "semantic_external_write",
        ),
        (
            "python",
            ["-m", "pip", "install", "example"],
            "environment_change",
        ),
        (
            "git",
            ["reset", "--hard", "HEAD"],
            "high_risk_local_git",
        ),
    ]
    for program, args, policy_id in cases:
        route = steering.route_generic_request(
            "run_process",
            {"program": program, "args": args, "root": "workspace"},
        )
        assert route["status"] == "specialized_required"
        assert route["execution_policy"]["policy_id"] == policy_id
        assert route["execution_policy"]["policy_version"] == POLICY_VERSION
        assert route["execution_policy"]["target_capability"] == (
            route["suggested_capabilities"][0]["name"]
        )
