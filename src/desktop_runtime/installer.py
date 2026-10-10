"""Fixed optional-component installer using reviewed specs and private environments."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib
import zipfile
import urllib.request

from desktop_runtime.components import ComponentProject, PROVIDER_ID
from desktop_runtime.config import atomic_write

UV_SHA256 = '1e9d1e0a766024a212d165dc67db2d7f71831a5ff4bb60ab7e6863391871c1d5'
UV_ARCHIVE_SHA256 = '7c38608c8a18ee137d748a1773053b07ec8f3a30fab49aebaa6f4e4efeceb019'
UV_URL = 'https://releases.astral.sh/github/uv/releases/download/0.12.24/uv-x86_64-pc-windows-msvc.zip'
PYTHON_VERSION = '3.11.9'


def validate_skill_package(raw):
    if not raw:
        raise ValueError('Select the reviewed Skill Library v0.5.0 source or wheel')
    path = Path(raw)
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
        raise ValueError('Select an absolute local package path')
    path = path.resolve()
    if path.is_dir():
        metadata = tomllib.loads((path / 'pyproject.toml').read_text(encoding='utf-8'))['project']
        if metadata.get('name') != 'chatgpt-skill-library' or metadata.get('version') != '0.5.0':
            raise ValueError('Expected chatgpt-skill-library v0.5.0')
        if not (path / 'src/skill_library/server.py').is_file():
            raise ValueError('Skill Library server source is missing')
    elif path.is_file() and path.suffix == '.whl':
        from email.parser import Parser
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
            if len(names) != 1:
                raise ValueError('Invalid Skill Library wheel metadata')
            metadata = Parser().parsestr(archive.read(names[0]).decode())
        if metadata.get('Name') != 'chatgpt-skill-library' or metadata.get('Version') != '0.5.0':
            raise ValueError('Expected chatgpt-skill-library v0.5.0 wheel')
    else:
        raise ValueError('Skill Library package was not found')
    return path


def package_sha256(path):
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    excluded = {'.git', '.cache', '.pytest_cache', '__pycache__', 'build', 'dist', '.venv'}
    digest = hashlib.sha256()
    count = total = 0
    for item in sorted(path.rglob('*')):
        relative = item.relative_to(path)
        if any(part in excluded for part in relative.parts):
            continue
        if item.is_symlink() or not item.resolve().is_relative_to(path):
            raise ValueError('Selected source package contains an external linked path')
        if item.is_file():
            count += 1
            total += item.stat().st_size
            if count > 5000 or total > 100_000_000:
                raise ValueError('Selected Skill Library source package is unexpectedly large')
            digest.update(relative.as_posix().encode())
            digest.update(b'\0')
            digest.update(hashlib.sha256(item.read_bytes()).digest())
    return digest.hexdigest()


class ComponentInstaller:
    def __init__(self, data, resources):
        self.project = ComponentProject(data, resources)
        self.resources = resources.resolve()
        self.uv = self.project.root / 'tools/uv.exe'
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(('UV_', 'PIP_', 'PYTHON', 'VIRTUAL_ENV', 'CONDA_'))}
        self.env.update(UV_PYTHON_INSTALL_DIR=str(data / 'components/managed-python'),
                        UV_CACHE_DIR=str(data / 'cache/uv'), UV_NO_CONFIG='1', UV_NO_PROJECT='1',
                        UV_INDEX_URL='https://pypi.org/simple',
                        PATH=str(self.resources) + os.pathsep + str(self.resources / 'node-runtime') + os.pathsep + self.env.get('PATH', ''))

    def plan(self, provider_id, skill_package=None):
        if not isinstance(provider_id, str) or not PROVIDER_ID.fullmatch(provider_id):
            raise ValueError('Invalid provider ID')
        specs = self.resources / 'provider-assets/provider_specs'
        paths = [specs / f'{provider_id}{suffix}' for suffix in ('.txt', '.npm.txt', '.source.json')]
        present = [path for path in paths if path.is_file()]
        if not present:
            raise ValueError('No reviewed bundled installer spec exists for this provider')
        package = validate_skill_package(skill_package) if provider_id == 'skill-library' else None
        if skill_package and provider_id != 'skill-library':
            raise ValueError('A local package is accepted only for Skill Library')
        return {'provider_id': provider_id, 'python_version': PYTHON_VERSION,
                'environment': str((self.resources if provider_id == 'browser' else self.project.root) / f'.provider_envs/{provider_id}'),
                'bundled_component': provider_id == 'browser',
                'specs': [{'name': path.name, 'content': path.read_text(encoding='utf-8'),
                           'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in present],
                'skill_package': str(package) if package else None,
                'skill_package_sha256': package_sha256(package) if package else None,
                'installer_tool': {'name': 'uv', 'version': '0.12.24', 'source': UV_URL,
                                   'archive_sha256': UV_ARCHIVE_SHA256, 'download_on_demand': True} if provider_id != 'browser' else None,
                'installs_into_private_data': provider_id != 'browser', 'activates_provider': False}

    def ensure_uv(self):
        if not self.uv.resolve().is_relative_to(self.project.root):
            raise ValueError('Installer executable escapes private component data')
        if self.uv.is_file():
            if hashlib.sha256(self.uv.read_bytes()).hexdigest() != UV_SHA256:
                raise ValueError('Private uv installer was modified; existing file preserved')
            return
        print('STEP Download verified optional uv 0.12.24 from its official release', flush=True)
        archive_path = self.project.data / 'cache/uv-0.12.24.zip'
        if not archive_path.resolve().is_relative_to(self.project.data):
            raise ValueError('Installer archive escapes private cache')
        if not archive_path.is_file():
            request = urllib.request.Request(UV_URL, headers={
                'User-Agent': 'PLA-Desktop/1.0 optional-component installer',
                'Accept': 'application/octet-stream'})
            with urllib.request.urlopen(request, timeout=60) as response:
                archive = response.read(100_000_001)
            if len(archive) > 100_000_000 or hashlib.sha256(archive).hexdigest() != UV_ARCHIVE_SHA256:
                raise ValueError('Optional installer archive checksum mismatch; no code executed')
            atomic_write(archive_path, archive)
        if hashlib.sha256(archive_path.read_bytes()).hexdigest() != UV_ARCHIVE_SHA256:
            raise ValueError('Cached installer archive checksum mismatch; file preserved')
        with zipfile.ZipFile(archive_path) as archive:
            binary = archive.read('uv.exe')
        if hashlib.sha256(binary).hexdigest() != UV_SHA256:
            raise ValueError('Optional installer executable checksum mismatch')
        atomic_write(self.uv, binary)

    def run(self, command):
        print('STEP ' + command[1], flush=True)
        subprocess.run(command, cwd=self.project.root, env=self.env, check=True,
                       timeout=600, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def install(self, provider_id, skill_package=None):
        plan = self.plan(provider_id, skill_package)
        self.project.stage(int(os.environ.get('PLA_BROWSER_PORT', '18931')))
        specs = self.resources / 'provider-assets/provider_specs'
        python_spec = specs / f'{provider_id}.txt'
        node_spec = specs / f'{provider_id}.npm.txt'
        source_spec = specs / f'{provider_id}.source.json'
        if provider_id == 'browser':
            package = self.resources / '.provider_envs/browser/node_modules/@playwright/mcp'
            if not (self.resources / 'node.exe').is_file() or not (package / 'cli.js').is_file():
                raise ValueError('Bundled browser component is missing; repair with the verified Desktop installer')
            version = json.loads((package / 'package.json').read_text())['version']
            expected = [line.strip().split('@')[-1] for line in node_spec.read_text().splitlines() if line.strip() and not line.lstrip().startswith('#')]
            if expected != [version]:
                raise ValueError('Bundled browser version does not match its reviewed specification; repair installation')
            return {'status': 'bundled_files_verified', 'provider_id': provider_id, 'version': version,
                    'activated': False, 'mcp_connection_verified': False, 'private_duplicate_not_installed': True}
        if python_spec.is_file():
            lines = [line.strip() for line in python_spec.read_text().splitlines() if line.strip() and not line.lstrip().startswith('#')]
            if not lines or any(not re.fullmatch(r'[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9_.+-]+', line) for line in lines):
                raise ValueError('Bundled Python specifications must pin exact package versions')
            self.ensure_uv()
            environment = Path(plan['environment']).resolve()
            if not environment.is_relative_to(self.project.root):
                raise ValueError('Provider environment escapes private data')
            python = environment / 'Scripts/python.exe'
            if not python.is_file():
                self.run([str(self.uv), 'venv', '--no-config', '--no-project', '--python', PYTHON_VERSION,
                          '--managed-python', str(environment)])
            self.run([str(self.uv), 'pip', 'install', '--no-config', '--python', str(python),
                      '--index-url', 'https://pypi.org/simple', '-r', str(python_spec)])
            if plan['skill_package']:
                self.run([str(self.uv), 'pip', 'install', '--no-config', '--python', str(python),
                          '--no-deps', plan['skill_package']])
                self.run([str(python), '-I', '-c', 'from importlib.metadata import version; from skill_library.server import mcp; assert version("chatgpt-skill-library") == "0.5.0"; assert mcp.name == "Skill Library"'])
        if node_spec.is_file():
            lines = [line.strip() for line in node_spec.read_text().splitlines() if line.strip() and not line.lstrip().startswith('#')]
            if not lines or any(not re.fullmatch(r'(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+@[0-9][A-Za-z0-9_.+-]*', line) for line in lines):
                raise ValueError('Bundled npm specifications must pin exact package versions')
            node = self.resources / 'node.exe'
            npm = self.resources / 'node-runtime/node_modules/npm/bin/npm-cli.js'
            if not node.is_file() or not npm.is_file():
                raise ValueError('Bundled Node/npm component is missing')
            target = self.project.root / f'.provider_envs/{provider_id}'
            if not target.resolve().is_relative_to(self.project.root):
                raise ValueError('Node environment escapes private data')
            self.run([str(node), str(npm), 'install', '--prefix', str(target), '--ignore-scripts',
                      '--no-audit', '--no-fund', '--registry=https://registry.npmjs.org', *lines])
        if source_spec.is_file():
            # Fixed original reviewed Git/npm helper; Git remains an observable
            # optional system prerequisite, never installed or elevated silently.
            from provider.source_provider_setup import setup_git_npm_source
            prior = os.environ.copy()
            try:
                os.environ.clear(); os.environ.update(self.env)
                setup_git_npm_source(self.project.root, provider_id, source_spec, timeout_seconds=600)
            finally:
                os.environ.clear(); os.environ.update(prior)
        receipt = {**plan, 'status': 'installed', 'activated': False}
        atomic_write(self.project.root / f'receipts/{provider_id}.json', json.dumps(receipt, indent=2).encode())
        return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--provider-id', required=True)
    parser.add_argument('--skill-package')
    parser.add_argument('--expected-plan-sha256')
    args = parser.parse_args()
    installer = ComponentInstaller(args.data_dir, args.resources)
    plan = installer.plan(args.provider_id, args.skill_package)
    if args.expected_plan_sha256 and hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest() != args.expected_plan_sha256:
        raise ValueError('Installation plan or package changed after review')
    print(json.dumps(installer.install(args.provider_id, args.skill_package)), flush=True)
