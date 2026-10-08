import asyncio
from types import SimpleNamespace

import pytest

from tooling import local_tools
import server


def test_runtime_external_process_descriptor_requires_confirmation():
    detail = server.CAPABILITY_REGISTRY.describe("runtime.external_process")
    assert detail["available"] is True
    assert detail["risk_level"] == "write_external"
    assert detail["requires_confirmation"] is True
    assert detail["requires_transaction"] is False
    assert "external-write" in detail["tags"]


def test_runtime_external_process_rejects_without_invoke(monkeypatch):
    called = False

    def fake_run(**_kwargs):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(local_tools, "run_confirmed_external_process", fake_run)

    with pytest.raises(PermissionError, match="requires confirmation='INVOKE'"):
        asyncio.run(
            server.capability_invoke(
                "runtime.external_process",
                {
                    "program": "gh",
                    "args": ["issue", "create", "--title", "x", "--body", "y"],
                },
            )
        )
    assert called is False


def test_runtime_external_process_enters_handler_only_after_invoke(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {
            "returncode": 0,
            "command_semantic": {
                "risk_level": "write_external",
                "action": "issue.create",
            },
        }

    monkeypatch.setattr(local_tools, "run_confirmed_external_process", fake_run)
    result = asyncio.run(
        server.capability_invoke(
            "runtime.external_process",
            {
                "program": "gh",
                "args": ["issue", "create", "--title", "x", "--body", "y"],
                "root": "workspace",
            },
            confirmation="INVOKE",
        )
    )

    assert result["status"] == "completed"
    assert result["data"]["returncode"] == 0
    assert captured["program"] == "gh"
    assert captured["root"] == "workspace"


def test_confirmed_external_process_rejects_non_external_semantic(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    with pytest.raises(ValueError, match="only accepts commands classified by semantic_external_write policy"):
        local_tools.run_confirmed_external_process(
            program="python",
            args=["-c", "print('x')"],
        )


def test_confirmed_external_process_preserves_semantics_and_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path.resolve())
    captured = {}

    def fake_run(request):
        captured["request"] = request
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(local_tools, "run_oneshot", fake_run)
    result = local_tools.run_confirmed_external_process(
        program="gh",
        args=["issue", "create", "--title", "x", "--body", "y"],
    )

    assert result["returncode"] == 0
    assert result["command_semantic"]["risk_level"] == "write_external"
    assert result["confirmation_required"] is True
    assert result["confirmation_supplied"] is True
    assert result["execution_policy"]["policy_id"] == "semantic_external_write"
    permissions = captured["request"].permissions.to_dict()
    assert permissions["confirmation_required"] is True
    assert permissions["confirmation_supplied"] is True
    assert permissions["semantic_risk_level"] == "write_external"
    assert permissions["network_intent"] == "write"
    assert permissions["execution_policy_id"] == "semantic_external_write"
    assert permissions["execution_policy_version"] == "1"
