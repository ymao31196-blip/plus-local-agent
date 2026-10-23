"""Reviewed user-owned MCP dependency specifications.

The installed application is immutable. Custom Python/npm dependencies live under
Desktop private data, never in PLA source, bundled resources, or the Runtime venv.
This is an opt-in package installer, not an arbitrary shell/script runner.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from desktop_runtime.config import atomic_write
from provider.provider_manifest import load_provider_manifests

PYTHON_PIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9_.+-]+\Z")
NPM_PIN = re.compile(r"(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+@[0-9][a-zA-Z0-9_.+-]*\Z")


def package_spec(project, provider_id, package_kind, packages, confirmed=False, expected_sha256=None):
    """Preview and explicitly commit exact user-owned package pins.

    Only a previously imported custom manifest is eligible. This does not
    execute code, enable the provider or grant the package any privileges.
    """
    if not isinstance(provider_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", provider_id):
        raise ValueError("Invalid custom provider ID")
    if package_kind not in ("python", "npm"):
        raise ValueError("Supported custom installation kinds are python and npm")
    if not isinstance(packages, list) or not 1 <= len(packages) <= 24 or any(
        not isinstance(item, str) or len(item) > 120 for item in packages
    ):
        raise ValueError("Specify between 1 and 24 exactly pinned packages")
    pattern = PYTHON_PIN if package_kind == "python" else NPM_PIN
    if any(not pattern.fullmatch(item) for item in packages) or len(packages) != len(set(packages)):
        raise ValueError("Only distinct exact PyPI/npm package pins are supported; no URLs, Git, scripts or paths")
    if (project.resources / "provider-assets/provider_manifests" / f"{provider_id}.json").exists():
        raise ValueError("Bundled provider specifications cannot be overridden")
    if any((project.resources / "provider-assets/provider_specs" / f"{provider_id}{suffix}").exists()
           for suffix in (".txt", ".npm.txt", ".source.json")):
        raise ValueError("Built-in provider installation is managed by PLA")
    manifests = load_provider_manifests(project.root, project.manifest_dir)
    if provider_id not in manifests:
        raise ValueError("Import and review the custom provider manifest before installing packages")
    manifest = manifests[provider_id]
    if package_kind == "python":
        if manifest.runtime_kind != "isolated_python_stdio" or manifest.python_path != (
            project.root / ".provider_envs" / provider_id / "Scripts/python.exe"
        ).resolve():
            raise ValueError("Python custom provider must use its own managed .provider_envs/<id>/Scripts/python.exe")
    else:
        if manifest.runtime_kind != "executable_stdio" or manifest.command_path != (
            project.resources / "node.exe"
        ).resolve() or manifest.cwd != (project.root / ".provider_envs" / provider_id).resolve():
            raise ValueError("npm custom provider must use bundled node.exe and its own managed .provider_envs/<id> cwd")
    suffix = ".txt" if package_kind == "python" else ".npm.txt"
    path = project.root / "custom_specs" / (provider_id + suffix)
    alternate = project.root / "custom_specs" / (provider_id + (".npm.txt" if package_kind == "python" else ".txt"))
    if alternate.exists():
        raise ValueError("The existing custom provider environment uses a different package kind")
    current = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    manifest_sha = hashlib.sha256(manifest.path.read_bytes()).hexdigest()
    content = "\n".join(packages) + "\n"
    payload = {"provider_id": provider_id, "package_kind": package_kind, "packages": packages,
               "manifest_sha256": manifest_sha, "current_spec_sha256": current}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    result = {**payload, "sha256": digest, "install_directory": str(project.root / ".provider_envs" / provider_id),
              "spec_file": str(path), "committed": False, "installed": False, "enabled": False,
              "warning": "Third-party packages execute code after installation; installing does not enable or trust their MCP tools."}
    if confirmed:
        if digest != expected_sha256:
            raise ValueError("The custom installation plan changed; preview again")
        # No privileges, no shell and no writes to packaged application resources.
        atomic_write(path, content.encode("utf-8"))
        result["committed"] = True
    return result


def get_spec_paths(project, provider_id):
    """Built-in specs take priority; external specs cannot shadow them."""
    bundled = project.resources / "provider-assets/provider_specs"
    names = [f"{provider_id}{suffix}" for suffix in (".txt", ".npm.txt", ".source.json")]
    builtins = [bundled / name for name in names if (bundled / name).is_file()]
    if builtins:
        return builtins
    if (project.resources / "provider-assets/provider_manifests" / (provider_id + ".json")).is_file():
        return []  # An official manifest never accepts an unreviewed user spec.
    if provider_id not in load_provider_manifests(project.root, project.manifest_dir):
        return []  # Installing a package never implicitly imports a Provider.
    custom = project.root / "custom_specs"
    paths = [custom / name for name in names[:2] if (custom / name).is_file()]
    if len(paths) > 1 or any(path.is_symlink() or not path.resolve().is_relative_to(project.root)
                             for path in paths):
        raise ValueError("Untrusted or conflicting custom package specifications")
    return paths
