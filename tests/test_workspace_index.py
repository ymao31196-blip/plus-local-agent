"""QMD optional workspace-index boundaries and core routing tests."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tooling import local_tools
from tooling import workspace_index as index
from capabilities.capability_registry import CapabilityRegistry
from capabilities.capability_broker import CapabilityBroker
from capabilities.core_capabilities import register_core_transaction_capabilities
from mcp_runtime.mcp_client_manager import MCPClientManager
from transactions.transaction_runtime import ActionTransactionStore


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(local_tools, "WORKSPACE", tmp_path)
    return tmp_path


def _make(root):
    directory = root / "research"
    directory.mkdir()
    (directory / "a.md").write_text("# WRF风资源\n\n复杂地形\n", encoding="utf-8")
    return directory


def _init(root, monkeypatch):
    directory = _make(root)
    monkeypatch.setattr(index, '_engine', lambda: (Path('node'), Path('qmd')))
    monkeypatch.setattr(index, "_execute", lambda *a, **k: "2 new")
    result = index.index_init(root="workspace", directory="research")
    assert result["indexed_files_preflight"] == 1
    return directory


def test_unavailable_optional_engine_does_not_create_index(root, monkeypatch):
    directory = _make(root)
    def unavailable():
        raise RuntimeError("QMD optional engine missing")
    monkeypatch.setattr(index, "_engine", unavailable)
    with pytest.raises(RuntimeError, match="missing"):
        index.index_init(root="workspace", directory="research")
    assert not (directory / ".qmd").exists()


def test_init_and_incremental_refresh_preserve_local_index(root, monkeypatch):
    directory = _init(root, monkeypatch)
    assert (directory / ".qmd/index.yml").is_file()
    assert index.index_refresh(root="workspace", directory="research")["indexed_files_preflight"] == 1
    assert index.index_status(root="workspace", directory="research")["status"] == "completed"
    with pytest.raises(FileExistsError):
        index.index_init(root="workspace", directory="research")


def test_root_escape_and_protected_root_denied(root):
    with pytest.raises(ValueError, match="outside workspace"):
        index.index_init(root="workspace", directory="../outside")
    with pytest.raises(PermissionError, match="protected"):
        index.index_status(root="pla", directory=".")


def test_symlink_inside_workspace_denied(root, monkeypatch):
    directory = _make(root)
    try:
        (directory / "shortcut.md").symlink_to(root / "other.md")
    except (NotImplementedError, OSError):
        pytest.skip("Symlinks unavailable in this environment")
    monkeypatch.setattr(index, "_execute", lambda *a, **k: "ok")
    with pytest.raises(ValueError, match="symlink|reparse"):
        index.index_init(root="workspace", directory="research")
    assert not (directory / ".qmd").exists()


def test_oversize_markdown_denied_before_creation(root, monkeypatch):
    directory = _make(root)
    (directory / "huge.md").write_bytes(b"x" * (index.MAX_DOC_BYTES + 1))
    monkeypatch.setattr(index, "_execute", lambda *a, **k: "ok")
    with pytest.raises(ValueError, match="limit"):
        index.index_init(root="workspace", directory="research")
    assert not (directory / ".qmd").exists()


def test_nonmd_and_ignored_dependency_files_not_indexed(root, monkeypatch):
    directory = _make(root)
    ignored = directory / "node_modules"
    ignored.mkdir()
    (ignored / "secret.md").write_text("secret", encoding="utf-8")
    (directory / "image.png").write_bytes(b"x")
    monkeypatch.setattr(index, "_execute", lambda *a, **k: "ok")
    result = index.index_init(root="workspace", directory="research")
    assert result["indexed_files_preflight"] == 1
    config = index.yaml.safe_load((directory / ".qmd/index.yml").read_text(encoding="utf-8"))
    assert "**/node_modules/**" in config["collections"]["pla"]["ignore"]


@pytest.mark.parametrize("injection", [
    {"update": {"run": "powershell"}},
    {"collections": {"pla": {"path": "..", "pattern": "**/*.md", "ignore": index.IGNORES}}},
    {"models": {"embed": "https://attacker.invalid/model"}},
])
def test_existing_untrusted_config_rejected(root, monkeypatch, injection):
    directory = _init(root, monkeypatch)
    config = index.yaml.safe_load((directory / ".qmd/index.yml").read_text(encoding="utf-8"))
    config.update(injection)
    (directory / ".qmd/index.yml").write_text(index.yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(PermissionError, match="Unreviewed"):
        index.index_refresh(root="workspace", directory="research")


def test_existing_foreign_qmd_index_not_overwritten(root, monkeypatch):
    directory = _make(root)
    (directory / ".qmd").mkdir()
    (directory / ".qmd/index.yml").write_text("collections: {}\n", encoding="utf-8")
    monkeypatch.setattr(index, "_execute", lambda *a, **k: "ok")
    with pytest.raises(FileExistsError):
        index.index_init(root="workspace", directory="research")


def test_qmd_builtin_models_allowed_without_external_model_urls(root, monkeypatch):
    directory = _init(root, monkeypatch)
    config = index.yaml.safe_load((directory / ".qmd/index.yml").read_text(encoding="utf-8"))
    config["models"] = dict(index.DEFAULT_MODELS)
    (directory / ".qmd/index.yml").write_text(index.yaml.safe_dump(config), encoding="utf-8")
    index.index_status(root="workspace", directory="research")


def test_search_returns_validated_bounded_relative_path(root, monkeypatch):
    _init(root, monkeypatch)
    def found(_target, action, *a):
        return json.dumps([{
            "file": "qmd://pla/a.md", "title": "WRF",
            "line": 3, "score": 0.92, "snippet": "safe"
        }])
    monkeypatch.setattr(index, "_execute", found)
    result = index.index_search(root="workspace", directory="research", query="WRF")
    assert result["count"] == 1
    assert result["results"][0]["path"] == "research/a.md"


@pytest.mark.parametrize("uri", [
    "qmd://pla/../../outside.md",
    "qmd://other/a.md",
    "qmd://pla/%2e%2e/outside.md",
    "qmd://pla/C:\\Windows\\secrets.md",
    "qmd://pla/node_modules/secret.md",
])
def test_search_rejects_foreign_or_traversal_results(root, monkeypatch, uri):
    _init(root, monkeypatch)
    monkeypatch.setattr(index, "_execute", lambda *a, **k: json.dumps([{
        "file": uri, "title": "unsafe", "line": 1, "score": 0.1, "snippet": "bad"
    }]))
    with pytest.raises(PermissionError):
        index.index_search(root="workspace", directory="research", query="WRF")


def test_stale_index_result_not_returned(root, monkeypatch):
    _init(root, monkeypatch)
    monkeypatch.setattr(index, "_execute", lambda *a, **k: json.dumps([{"file":"qmd://pla/gone.md"}]))
    assert index.index_search(root="workspace", directory="research", query="x")["count"] == 0


def test_bounded_query_and_result_counts(root, monkeypatch):
    _init(root, monkeypatch)
    with pytest.raises(ValueError, match="1–120"):
        index.index_search(root="workspace", directory="research", query="x" * 121)
    with pytest.raises(ValueError, match="limit"):
        index.index_search(root="workspace", directory="research", query="x", limit=21)
    monkeypatch.setattr(index, "_execute", lambda *a, **k: json.dumps([
        {"file":"qmd://pla/a.md"}] * 12))
    with pytest.raises(ValueError, match="Unbounded"):
        index.index_search(root="workspace", directory="research", query="x", limit=10)


def test_core_index_descriptors_and_confirmation_boundary(root, monkeypatch):
    registry=CapabilityRegistry()
    manager=MCPClientManager(registry)
    broker=CapabilityBroker(registry,manager)
    store=ActionTransactionStore()
    try:
        register_core_transaction_capabilities(registry,broker,store)
        init=registry.describe("core.workspace_index_init")
        search=registry.describe("core.workspace_index_search")
        assert init["requires_confirmation"] is True
        assert init["risk_level"] == "write_local"
        assert search["requires_confirmation"] is False
        assert search["risk_level"] == "read"
        _make(root)
        with pytest.raises(Exception, match="confirmation"):
            asyncio.run(broker.invoke("core.workspace_index_init",{"directory":"research"}))
        monkeypatch.setattr(index,"_execute",lambda *a,**kw:"updated")
        result=asyncio.run(broker.invoke("core.workspace_index_init",{"directory":"research"},confirmation="INVOKE"))
        assert result["data"]["operation"] == "init"
    finally:
        store.close()
