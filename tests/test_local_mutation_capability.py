import asyncio
from types import SimpleNamespace

import pytest

from tooling import local_tools
import server


def test_local_mutation_descriptors_require_confirmation():
    environment = server.CAPABILITY_REGISTRY.describe("runtime.environment_process")
    local_git = server.CAPABILITY_REGISTRY.describe("runtime.local_mutation_process")

    for detail in (environment, local_git):
        assert detail["available"] is True
        assert detail["risk_level"] == "write_local"
        assert detail["requires_confirmation"] is True
        assert detail["requires_transaction"] is False
    assert "environment-change" in environment["tags"]
    assert "local-mutation" in local_git["tags"]


def test_environment_process_rejects_without_invoke(monkeypatch):
    called = False

    def fake_run(**_kwargs):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(local_tools, "run_confirmed_environment_process", fake_run)
    with pytest.raises(PermissionError, match="requires confirmation='INVOKE'"):
        asyncio.run(
            server.capability_invoke(
                "runtime.environment_process",
                {"program": "python", "args": ["-m", "pip", "install", "example"]},
            )
        )
    assert called is False


def test_local_mutation_process_rejects_without_invoke(monkeypatch):
    called = False

    def fake_run(**_kwargs):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(local_tools, "run_confirmed_local_mutation_process", fake_run)
    with pytest.raises(PermissionError, match="requires confirmation='INVOKE'"):
        asyncio.run(
            server.capability_invoke(
                "runtime.local_mutation_process",
                {"program": "git", "args": ["reset", "--hard", "HEAD~1"]},
            )
        )
    assert called is False


def test_confirmed_local_capabilities_enter_handlers_only_after_invoke(monkeypatch):
    environment = {}
    local_git = {}

    def fake_environment(**kwargs):
        environment.update(kwargs)
        return {"returncode": 0}

    def fake_local_git(**kwargs):
        local_git.update(kwargs)
        return {"returncode": 0}

    monkeypatch.setattr(local_tools, "run_confirmed_environment_process", fake_environment)
    monkeypatch.setattr(local_tools, "run_confirmed_local_mutation_process", fake_local_git)

    first = asyncio.run(
        server.capability_invoke(
            "runtime.environment_process",
            {"program": "python", "args": ["-m", "pip", "install", "example"]},
            confirmation="INVOKE",
        )
    )
    second = asyncio.run(
        server.capability_invoke(
            "runtime.local_mutation_process",
            {"program": "git", "args": ["reset", "--hard", "HEAD~1"]},
            confirmation="INVOKE",
        )
    )

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert environment["program"] == "python"
    assert local_git["program"] == "git"


def test_environment_process_revalidates_semantics(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())

    with pytest.raises(ValueError, match="does not accept command semantic"):
        local_tools.run_confirmed_environment_process(
            program="python",
            args=["-m", "pip", "list"],
        )
    with pytest.raises(ValueError, match="does not accept command semantic"):
        local_tools.run_confirmed_environment_process(
            program="python",
            args=["-c", "print('x')"],
        )


def test_local_mutation_process_revalidates_git_action(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())

    with pytest.raises(ValueError, match="does not accept command semantic"):
        local_tools.run_confirmed_local_mutation_process(
            program="git",
            args=["commit", "-m", "x"],
        )
    with pytest.raises(ValueError, match="does not accept command semantic"):
        local_tools.run_confirmed_local_mutation_process(
            program="python",
            args=["-c", "print('x')"],
        )


def test_confirmed_environment_process_preserves_confirmation_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    captured = {}

    def fake_run(request):
        captured["request"] = request
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(local_tools, "run_oneshot", fake_run)
    result = local_tools.run_confirmed_environment_process(
        program="python",
        args=["-m", "pip", "install", "example"],
    )

    assert result["returncode"] == 0
    assert result["command_semantic"]["effect_class"] == "environment_change"
    assert result["execution_policy"]["policy_id"] == "environment_change"
    assert result["confirmation_capability"] == "runtime.environment_process"
    permissions = captured["request"].permissions.to_dict()
    assert permissions["confirmation_required"] is True
    assert permissions["confirmation_supplied"] is True
    assert permissions["semantic_effect_class"] == "environment_change"
    assert permissions["execution_policy_id"] == "environment_change"
    assert permissions["execution_policy_version"] == "1"


def test_confirmed_git_mutation_preserves_confirmation_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    captured = {}

    def fake_run(request):
        captured["request"] = request
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(local_tools, "run_oneshot", fake_run)
    result = local_tools.run_confirmed_local_mutation_process(
        program="git",
        args=["reset", "--hard", "HEAD~1"],
    )

    assert result["returncode"] == 0
    assert result["command_semantic"]["action"] == "reset"
    assert result["execution_policy"]["policy_id"] == "high_risk_local_git"
    assert result["confirmation_capability"] == "runtime.local_mutation_process"
    permissions = captured["request"].permissions.to_dict()
    assert permissions["confirmation_required"] is True
    assert permissions["confirmation_supplied"] is True
    assert permissions["semantic_action"] == "reset"
    assert permissions["execution_policy_id"] == "high_risk_local_git"
    assert permissions["execution_policy_version"] == "1"
