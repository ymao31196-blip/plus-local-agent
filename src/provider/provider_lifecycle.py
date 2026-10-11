"""Provider lifecycle management for installation, upgrade, and uninstallation.

Manages the complete lifecycle of external MCP providers including environment
setup, dependency installation, health checks, and cleanup.
"""
from __future__ import annotations

import hashlib
import json
import re
import os
import uuid
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

try:
    from provider.provider_repository import ProviderPackage
except ImportError:
    from .provider_repository import ProviderPackage


class ProviderState(Enum):
    """Provider lifecycle states."""
    NOT_INSTALLED = "not_installed"
    INSTALLING = "installing"
    INSTALLED = "installed"
    ENABLED = "enabled"
    DISABLED = "disabled"
    UPGRADING = "upgrading"
    UNINSTALLING = "uninstalling"
    FAILED = "failed"
    HEALTH_CHECK_FAILED = "health_check_failed"


@dataclass
class ProviderRecord:
    """Persistent provider installation record."""
    provider_id: str
    version: str
    installed_at: str
    state: str
    repository_url: str
    install_spec_hash: str
    manifest_hash: str
    environment_path: str
    assets: dict[str, str]  # relative path -> sha256
    metadata: dict[str, Any] = field(default_factory=dict)
    health_check_last: str | None = None
    health_check_status: str | None = None
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'provider_id': self.provider_id,
            'version': self.version,
            'installed_at': self.installed_at,
            'state': self.state,
            'repository_url': self.repository_url,
            'install_spec_hash': self.install_spec_hash,
            'manifest_hash': self.manifest_hash,
            'environment_path': self.environment_path,
            'assets': self.assets,
            'metadata': self.metadata,
            'health_check_last': self.health_check_last,
            'health_check_status': self.health_check_status,
            'failure_reason': self.failure_reason
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ProviderRecord:
        return ProviderRecord(
            provider_id=data['provider_id'],
            version=data['version'],
            installed_at=data['installed_at'],
            state=data['state'],
            repository_url=data['repository_url'],
            install_spec_hash=data['install_spec_hash'],
            manifest_hash=data['manifest_hash'],
            environment_path=data['environment_path'],
            assets=data.get('assets', {}),
            metadata=data.get('metadata', {}),
            health_check_last=data.get('health_check_last'),
            health_check_status=data.get('health_check_status'),
            failure_reason=data.get('failure_reason')
        )


class ProviderLifecycleManager:
    """Versioned, owned installations; all paths and bytes are checked before mutation."""

    def __init__(self, component_root, manifest_dir, uv_exe, python_version="3.11.9", resources=None):
        self.component_root = Path(component_root).resolve()
        self.manifest_dir = Path(manifest_dir).resolve()
        self.uv_exe = Path(uv_exe).resolve()
        self.resources = Path(resources).resolve() if resources else None
        if not self.manifest_dir.is_relative_to(self.component_root):
            raise ValueError('Manifest directory escapes component root')
        self.python_version = python_version
        self.state_file = self.component_root / 'provider_states.json'
        self._hooks = {name: [] for name in ('pre_install', 'post_install', 'pre_uninstall', 'post_uninstall')}

    def _inside(self, relative):
        from provider.provider_repository import checked_relative_path
        checked_relative_path(relative)
        path = self.component_root / relative
        if not path.resolve().is_relative_to(self.component_root):
            raise ValueError('Component path escapes private data')
        return path

    def _paths(self, provider_id, version):
        from provider.provider_repository import checked_identity
        checked_identity(provider_id, version)
        return (self._inside(f'.provider_envs/{provider_id}/{version}'),
                self._inside(f'repository-assets/{provider_id}/{version}'))

    def _load_states(self):
        if not self.state_file.exists():
            return {}
        data = json.loads(self.state_file.read_text(encoding='utf-8'))
        if data.get('schema_version') != 1 or not isinstance(data.get('providers'), dict):
            raise ValueError('Invalid provider state file; existing data preserved')
        return {key: ProviderRecord.from_dict(value) for key, value in data['providers'].items()}

    def _save_states(self, states):
        from desktop_runtime.config import atomic_write
        atomic_write(self.state_file, json.dumps({'schema_version': 1, 'providers':
                     {key: value.to_dict() for key, value in states.items()}}, indent=2).encode())

    def get_provider_state(self, provider_id):
        from provider.provider_repository import checked_identity
        checked_identity(provider_id)
        return self._load_states().get(provider_id)

    def list_installed_providers(self):
        return list(self._load_states().values())

    def _asset_paths(self, record):
        _, namespace = self._paths(record.provider_id, record.version)
        from provider.provider_repository import checked_relative_path
        result = {}
        for name, digest in record.assets.items():
            rel = checked_relative_path(name)
            if not name.startswith('assets/'):
                raise ValueError('Provider asset must be inside assets/')
            target = namespace.joinpath(*rel.parts[1:])
            if not target.resolve().is_relative_to(namespace.resolve()) or not namespace.resolve().is_relative_to(self.component_root):
                raise ValueError('Provider asset escapes its namespace')
            result[target] = digest
        return result

    def _manifest_path(self, provider_id):
        from provider.provider_repository import checked_identity
        checked_identity(provider_id)
        return self._inside('provider_manifests/' + provider_id + '.json')

    def _verify_version_files(self, record):
        env, _ = self._paths(record.provider_id, record.version)
        if Path(record.environment_path).resolve() != env.resolve() or record.metadata.get('layout') != 'versioned-v1':
            raise ValueError('Cannot determine installation ownership; existing files preserved')
        for path, digest in self._asset_paths(record).items():
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('Installed asset changed; existing files preserved')

    def _owned_versions(self, record):
        versions = [record]
        for value in record.metadata.get('previous_versions', []):
            previous = ProviderRecord.from_dict(value)
            if previous.provider_id != record.provider_id or previous.version in {row.version for row in versions}:
                raise ValueError('Invalid previous version ownership')
            versions.append(previous)
        return versions

    def _verify_record(self, record):
        manifest = self._manifest_path(record.provider_id)
        if not manifest.is_file() or hashlib.sha256(manifest.read_bytes()).hexdigest() != record.manifest_hash:
            raise ValueError('Installed manifest changed; existing files preserved')
        for version in self._owned_versions(record):
            self._verify_version_files(version)

    def _run(self, command):
        import os
        from runtime.runtime_context import terminate_owned_process_tree
        allowed = {'SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA','PATH','PATHEXT',
                   'HOMEDRIVE','HOMEPATH','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','SSL_CERT_FILE','SSL_CERT_DIR'}
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        env.update(UV_PYTHON_INSTALL_DIR=str(self.component_root / 'managed-python'),
                   UV_CACHE_DIR=str(self.component_root.parent / 'cache/uv'), UV_NO_CONFIG='1', UV_NO_PROJECT='1',
                   UV_INDEX_URL='https://pypi.org/simple', PYTHONDONTWRITEBYTECODE='1')
        child = subprocess.Popen(command, cwd=self.component_root, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            output, _ = child.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            terminate_owned_process_tree(child)
            raise ValueError('Dependency operation exceeded its time limit')
        if child.returncode:
            raise ValueError(f'Dependency operation failed: {output[-4000:]}')

    def _prepare_manifest(self, package, env, namespace):
        from provider.provider_manifest import load_provider_manifest
        from provider.provider_repository import checked_relative_path
        manifest = json.loads(json.dumps(package.manifest))
        runtime = manifest['runtime']
        kind = runtime['kind']
        cwd = runtime.get('cwd', '.')
        if cwd != '.':
            checked_relative_path(cwd)
        target_cwd = namespace if cwd == '.' else namespace / cwd
        args = runtime.get('args', [])
        if kind == 'isolated_python_stdio':
            runtime['python'] = env.relative_to(self.component_root).as_posix() + '/Scripts/python.exe'
            if len(args) >= 2 and args[0] == '-m' and re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_.]*', args[1]):
                module = 'assets/providers/' + args[1].replace('.', '/') + '.py'
                if module in package.assets:
                    target_cwd = namespace / 'providers'
        elif kind == 'executable_stdio':
            command = runtime.get('command')
            if command in {'node', 'node.exe'} and self.resources:
                runtime['command'] = str(self.resources / 'node.exe')
                if package.spec.get('dependencies', {}).get('node_packages'):
                    runtime['args'] = ['--require', str(env / 'pla-node-path.cjs'), *[str(env / arg) if arg.startswith('node_modules/') else arg for arg in args]]
            else:
                checked_relative_path(command)
                runtime['command'] = (namespace / command).relative_to(self.component_root).as_posix()
        elif kind != 'streamable_http':
            raise ValueError('Unsupported repository runtime kind')
        if kind != 'streamable_http':
            runtime['cwd'] = target_cwd.relative_to(self.component_root).as_posix()
        manifest['autostart'] = False
        preview = self._inside('repository-validation/' + package.provider_id + '.json')
        from desktop_runtime.config import atomic_write
        atomic_write(preview, json.dumps(manifest).encode())
        try:
            load_provider_manifest(preview, self.component_root)
        finally:
            preview.unlink(missing_ok=True)
        return manifest

    def _install_dependencies(self, package, env):
        dependencies = package.spec.get('dependencies', {})
        python = dependencies.get('python_packages', [])
        node = dependencies.get('node_packages', [])
        if dependencies.get('system') or dependencies.get('providers') or any(package.spec.get('install_hooks', {}).values()):
            raise ValueError('Repository system dependencies and executable hooks are not supported')
        if any(not isinstance(item, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9_.+-]+', item) for item in python):
            raise ValueError('Python dependencies must use exact reviewed versions')
        if any(not isinstance(item, str) or not re.fullmatch(r'(?:@[a-zA-Z0-9_.-]+/)?[a-zA-Z0-9_.-]+@[0-9][a-zA-Z0-9_.+-]*', item) for item in node):
            raise ValueError('Node dependencies must use exact reviewed versions')
        if package.manifest['runtime']['kind'] == 'isolated_python_stdio':
            self._run([str(self.uv_exe), 'venv', '--no-config', '--no-project', '--python', self.python_version, '--managed-python', str(env)])
            if python:
                self._run([str(self.uv_exe), 'pip', 'install', '--no-config', '--python', str(env / 'Scripts/python.exe'), '--index-url', 'https://pypi.org/simple', *python])
        elif python:
            raise ValueError('Python dependencies require a Python runtime')
        if node:
            if not self.resources or not (self.resources / 'node.exe').is_file():
                raise ValueError('Managed Node component is missing')
            env.mkdir(parents=True, exist_ok=True)
            npm = self.resources / 'node-runtime/node_modules/npm/bin/npm-cli.js'
            from desktop_runtime.config import atomic_write
            atomic_write(env / 'pla-node-path.cjs', b"process.env.NODE_PATH = require('node:path').join(__dirname,'node_modules'); require('node:module').Module._initPaths();\n")
            self._run([str(self.resources / 'node.exe'), str(npm), 'install', '--prefix', str(env), '--ignore-scripts', '--save-exact', '--registry=https://registry.npmjs.org', *node])

    def install_provider(self, package, repository_url, progress_callback=None):
        return self._install(package, repository_url, progress_callback, upgrading=False)

    def upgrade_provider(self, provider_id, new_package, repository_url, progress_callback=None):
        if provider_id != new_package.provider_id:
            raise ValueError('Upgrade identity mismatch')
        return self._install(new_package, repository_url, progress_callback, upgrading=True)

    def _install(self, package, repository_url, progress_callback, upgrading):
        from desktop_runtime.config import atomic_write
        package.verify_checksums()
        report = progress_callback or (lambda *args: None)
        states = self._load_states()
        old = states.get(package.provider_id)
        manifest_path = self._manifest_path(package.provider_id)
        if upgrading:
            if old is None or old.version == package.version:
                raise ValueError('Select a different version of an installed provider')
            self._verify_record(old)
        elif old or manifest_path.exists():
            raise ValueError('Provider already exists; existing installation preserved')
        env, namespace = self._paths(package.provider_id, package.version)
        if env.exists() or namespace.exists():
            raise ValueError('Target version already contains files; existing data preserved')
        manifest = self._prepare_manifest(package, env, namespace)
        record = ProviderRecord(package.provider_id, package.version, datetime.utcnow().isoformat() + 'Z',
                                'installed', repository_url, hashlib.sha256(json.dumps(package.spec, sort_keys=True).encode()).hexdigest(),
                                '', str(env), {name: hashlib.sha256(data).hexdigest() for name, data in package.assets.items()},
                                metadata={'layout': 'versioned-v1'})
        if old:
            previous = old.to_dict()
            previous['metadata'] = {**old.metadata}
            previous['metadata'].pop('previous_versions', None)
            record.metadata['previous_versions'] = [*old.metadata.get('previous_versions', []), previous]
        # Validate every path before downloading dependencies or writing assets.
        paths = self._asset_paths(record)
        original_manifest = manifest_path.read_bytes() if old else None
        written = []
        try:
            report('Preparing isolated dependencies', 10)
            for hook in self._hooks['pre_install']:
                hook(package, record)
            self._install_dependencies(package, env)
            report('Writing versioned provider assets', 60)
            namespace.mkdir(parents=True, exist_ok=False)
            for name, data in package.assets.items():
                path = next(path for path in paths if path.relative_to(namespace).as_posix() == name.removeprefix('assets/'))
                self._inside(path.relative_to(self.component_root).as_posix())
                atomic_write(path, data)
                written.append(path)
            cwd = self.component_root / manifest['runtime'].get('cwd', '.')
            cwd.mkdir(parents=True, exist_ok=True)
            for hook in self._hooks['post_install']:
                hook(package, record)
            if old:
                self._verify_record(old)
            elif manifest_path.exists():
                raise ValueError('Manifest appeared during installation; file preserved')
            content = json.dumps(manifest, indent=2).encode()
            atomic_write(manifest_path, content)
            record.manifest_hash = hashlib.sha256(content).hexdigest()
            states[record.provider_id] = record
            report('Committing installation; enable separately to discover actual MCP tools', 95)
            self._save_states(states)
            return record
        except Exception:
            if manifest_path.exists() and record.manifest_hash and hashlib.sha256(manifest_path.read_bytes()).hexdigest() == record.manifest_hash:
                if original_manifest is not None:
                    atomic_write(manifest_path, original_manifest)
                else:
                    manifest_path.unlink()
            for path in written:
                self._inside(path.relative_to(self.component_root).as_posix())
                digest = paths[path]
                if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
                    path.unlink()
            for directory in sorted({path.parent for path in written}, key=lambda path: len(path.parts), reverse=True):
                while directory.is_relative_to(namespace) and directory.exists():
                    try: directory.rmdir()
                    except OSError: break
                    if directory == namespace: break
                    directory = directory.parent
            if namespace.exists():
                try: namespace.rmdir()
                except OSError: pass
            if env.exists():
                self._remove_environment(env)
            raise

    def _remove_environment(self, path):
        path = Path(path)
        anchor = self._inside('.provider_envs')
        if not path.resolve().is_relative_to(anchor.resolve()) or len(path.relative_to(anchor).parts) != 2:
            raise ValueError('Environment removal escapes owned version directory')
        if path.exists():
            shutil.rmtree(path)

    def uninstall_provider(self, provider_id, progress_callback=None):
        states = self._load_states()
        record = states.get(provider_id)
        if record is None:
            raise ValueError('Provider is not installed')
        self._verify_record(record)
        for hook in self._hooks['pre_uninstall']:
            hook(provider_id, record)
        # Move owned files first so any failure before state commit is reversible.
        staging = self._inside('.repository-trash/' + uuid.uuid4().hex)
        staging.mkdir(parents=True, exist_ok=False)
        moved = []
        try:
            targets = [self._manifest_path(provider_id)]
            for version in self._owned_versions(record):
                targets.extend(self._asset_paths(version))
                env = Path(version.environment_path)
                if env.exists(): targets.append(env)
            for index, target in enumerate(targets):
                self._inside(target.relative_to(self.component_root).as_posix())
                destination = staging / str(index)
                os.replace(target, destination)
                moved.append((target, destination))
            for hook in self._hooks['post_uninstall']:
                hook(provider_id, record)
            del states[provider_id]
            self._save_states(states)
        except BaseException:
            for original, staged in reversed(moved):
                self._inside(original.relative_to(self.component_root).as_posix())
                if original.exists():
                    raise ValueError('Uninstall rollback target changed; staged files preserved at ' + str(staging))
                os.replace(staged, original)
            staging.rmdir()
            raise
        # State commit makes the component unavailable. Cleanup owns only this
        # uniquely created transaction directory, never a prefix glob.
        try:
            self._inside(staging.relative_to(self.component_root).as_posix())
            deadline = time.monotonic() + 15
            while True:
                try:
                    shutil.rmtree(staging)
                    break
                except PermissionError:
                    if time.monotonic() >= deadline: raise
                    time.sleep(0.25)
        except OSError:
            if progress_callback:
                progress_callback('Component removed; owned cleanup directory retained: ' + str(staging), 100)

    def health_check(self, provider_id):
        record = self.get_provider_state(provider_id)
        if record is None:
            return {'provider_id': provider_id, 'healthy': False, 'status': 'not_installed'}
        try:
            self._verify_record(record)
            manifest = json.loads(self._manifest_path(provider_id).read_text(encoding='utf-8'))
            kind = manifest['runtime']['kind']
            executable = self.component_root / manifest['runtime'].get('python', manifest['runtime'].get('command', ''))
            healthy = kind == 'streamable_http' or executable.is_file()
            return {'provider_id': provider_id, 'version': record.version, 'state': record.state,
                    'healthy': healthy, 'status': 'installation_verified' if healthy else 'dependency_missing',
                    'scope': 'installation_integrity', 'mcp_connection_verified': False}
        except ValueError as error:
            return {'provider_id': provider_id, 'healthy': False, 'status': 'modified', 'error': str(error)}

    def register_hook(self, hook_type, callback):
        if hook_type not in self._hooks or not callable(callback):
            raise ValueError('Invalid lifecycle hook')
        self._hooks[hook_type].append(callback)
