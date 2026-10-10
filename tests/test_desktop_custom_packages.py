"""RC.7 custom MCP packages are gated independently of PLA source development."""
import json
from pathlib import Path

import pytest

from desktop_runtime.components import ComponentProject
from desktop_runtime.manager import Manager
from desktop_runtime.installer import ComponentInstaller


def _project(tmp_path, provider_id="user-py", kind="python"):
    resources = tmp_path / "installed"
    resources.mkdir(parents=True)
    project = ComponentProject(tmp_path / "private", resources)
    if kind == "python":
        runtime = {"kind": "isolated_python_stdio",
                   "python": f".provider_envs/{provider_id}/Scripts/python.exe",
                   "args": ["-m", "user_mcp"], "cwd": "."}
    else:
        (resources / "node.exe").write_bytes(b"test node placeholder")
        runtime = {"kind": "executable_stdio", "command": str(resources / "node.exe"),
                   "args": ["node_modules/example-mcp/server.js"],
                   "cwd": f".provider_envs/{provider_id}"}
    content = json.dumps({"schema_version": 1, "id": provider_id,
                          "runtime": runtime, "tool_allowlist": ["hello"]})
    proposal = project.import_manifest(content)
    project.import_manifest(content, confirm=True, expected_sha256=proposal["sha256"])
    return project


def _preview(project, packages, kind="python", provider_id="user-py"):
    from desktop_runtime.custom_packages import package_spec
    return package_spec(project, provider_id, kind, packages)


def test_custom_python_pins_preview_commit_and_installer_reuse(tmp_path):
    from desktop_runtime.custom_packages import package_spec
    project = _project(tmp_path)
    preview = _preview(project, ["fastmcp==4.0.3", "mcp==2.2.0"])
    path = Path(preview["spec_file"])
    assert not path.exists()
    assert preview["enabled"] is False
    with pytest.raises(ValueError, match="changed"):
        package_spec(project, "user-py", "python", ["fastmcp==4.0.3", "mcp==2.2.0"],
                     confirmed=True, expected_sha256="0"*64)
    committed = package_spec(project, "user-py", "python", ["fastmcp==4.0.3", "mcp==2.2.0"],
                             confirmed=True, expected_sha256=preview["sha256"])
    assert committed["committed"] is True
    assert path.read_text() == "fastmcp==4.0.3\nmcp==2.2.0\n"
    plan = ComponentInstaller(project.data, project.resources).plan("user-py")
    assert plan["source"] == "user_custom"
    assert plan["installs_into_private_data"] and not plan["activates_provider"]
    assert plan["specs"][0]["name"] == "user-py.txt"
    assert "fastmcp==4.0.3" in plan["specs"][0]["content"]
    assert project.catalog()["providers"][0]["requested_enabled"] is False
    assert not (project.resources / "provider-assets").exists()


@pytest.mark.parametrize("packages", [
    ["pkg"], ["pkg>=1.0"], ["-e ./local"], ["git+https://example.com/repo"],
    ["pkg==1.0;os_name=nt"], ["file:///tmp/a.whl"], ["pkg==1.0", "pkg==1.0"], [],
    ["pkg==1.0"]*25, ["../../evil==1.2"], ["https://example.com/mcp==1.0"],
])
def test_custom_package_refuses_unpinned_or_injectable_dependencies(tmp_path, packages):
    project = _project(tmp_path)
    with pytest.raises(ValueError):
        _preview(project, packages)
    assert not (project.root / "custom_specs").exists()


def test_custom_npm_pins_and_workdir_validation(tmp_path):
    from desktop_runtime.custom_packages import package_spec
    project = _project(tmp_path, "user-node", "npm")
    preview = _preview(project, ["@demo/mcp@1.2.3"], kind="npm", provider_id="user-node")
    package_spec(project, "user-node", "npm", ["@demo/mcp@1.2.3"],
                 confirmed=True, expected_sha256=preview["sha256"])
    plan = ComponentInstaller(project.data, project.resources).plan("user-node")
    assert plan["source"] == "user_custom"
    assert plan["specs"][0]["name"] == "user-node.npm.txt"
    with pytest.raises(ValueError, match="Python custom provider"):
        _preview(project, ["demo==1.0"], kind="python", provider_id="user-node")


def test_custom_cannot_override_or_install_before_manifest(tmp_path):
    from desktop_runtime.custom_packages import package_spec
    project = ComponentProject(tmp_path / "private", tmp_path / "installed")
    with pytest.raises(ValueError, match="Import and review"):
        package_spec(project, "new", "python", ["example==1.0"])
    project = _project(tmp_path / "another")
    builtin = project.resources / "provider-assets/provider_specs"
    builtin.mkdir(parents=True)
    (builtin / "user-py.txt").write_text("built-in==1.0\n")
    with pytest.raises(ValueError, match="Built-in"):
        _preview(project, ["example==1.0"])
    assert ComponentInstaller(project.data, project.resources).plan("user-py")["source"] == "bundled"


def test_custom_manifest_and_spec_change_requires_new_review(tmp_path):
    from desktop_runtime.custom_packages import package_spec
    project = _project(tmp_path)
    previous = _preview(project, ["fastmcp==4.0.3"])
    updated = _preview(project, ["fastmcp==4.0.4"])
    assert previous["sha256"] != updated["sha256"]
    with pytest.raises(ValueError, match="changed"):
        package_spec(project, "user-py", "python", ["fastmcp==4.0.4"],
                     confirmed=True, expected_sha256=previous["sha256"])
    path = project.manifest_dir / "user-py.json"
    data = json.loads(path.read_text())
    data["tool_allowlist"].append("version")
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="changed"):
        package_spec(project, "user-py", "python", ["fastmcp==4.0.3"],
                     confirmed=True, expected_sha256=previous["sha256"])


def test_manager_custom_package_bridge_and_catalog(tmp_path):
    manager = Manager(tmp_path / "private", tmp_path / "installed")
    project = manager.components
    # Staging the real custom manifest never changes full-source development rights.
    content = json.dumps({"schema_version": 1, "id": "user-mcp",
                          "runtime": {"kind": "isolated_python_stdio",
                                      "python": ".provider_envs/user-mcp/Scripts/python.exe",
                                      "args": ["-m", "my_mcp"], "cwd": "."},
                          "tool_allowlist": ["ping"]})
    p = manager.dispatch("provider_import", {"content": content, "confirmed": False, "expected_sha256": None})
    manager.dispatch("provider_import", {"content": content, "confirmed": True, "expected_sha256": p["sha256"]})
    req = {"provider_id": "user-mcp", "package_kind": "python", "packages": ["fastmcp==4.0.3"],
           "confirmed": False, "expected_sha256": None}
    preview = manager.dispatch("provider_package", req)
    assert not preview["committed"]
    with pytest.raises(ValueError, match="Review|changed"):
        manager.dispatch("provider_package", {**req, "confirmed": True, "expected_sha256": "bad"})
    manager.dispatch("provider_package", {**req, "confirmed": True, "expected_sha256": preview["sha256"]})
    catalog = manager.dispatch("provider_catalog", {})["providers"][0]
    assert catalog["install_supported"] is True and catalog["install_source"] == "user_custom"
    assert not manager.dispatch("development_status", {})["enabled"]
    assert manager.components.preferences() == {"enabled": []}


def test_custom_manifest_requires_fixed_owned_execution_environment(tmp_path):
    from desktop_runtime.custom_packages import package_spec
    project = ComponentProject(tmp_path / "private", tmp_path / "installed")
    foreign = json.dumps({"schema_version": 1, "id": "host-py",
                          "runtime": {"kind": "executable_stdio", "command": "python.exe",
                                      "args": ["-c", "print(1)"], "cwd": "."},
                          "tool_allowlist": ["ping"]})
    p = project.import_manifest(foreign)
    project.import_manifest(foreign, confirm=True, expected_sha256=p["sha256"])
    with pytest.raises(ValueError, match="own managed"):
        package_spec(project, "host-py", "python", ["fastmcp==4.0.3"])
    with pytest.raises(ValueError, match="bundled node"):
        package_spec(project, "host-py", "npm", ["demo@1.2.3"])
