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

from desktop_runtime.components import ComponentProject, PROVIDER_ID
from desktop_runtime.config import atomic_write

UV_SHA256 = '1e9d1e0a766024a212d165dc67db2d7f71831a5ff4bb60ab7e6863391871c1d5'
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
        self.uv = self.resources / 'uv.exe'
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
                'environment': str(self.project.root / f'.provider_envs/{provider_id}'),
                'specs': [{'name': path.name, 'content': path.read_text(encoding='utf-8'),
                           'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in present],
                'skill_package': str(package) if package else None,
                'skill_package_sha256': package_sha256(package) if package else None,
                'installs_into_private_data': True, 'activates_provider': False}

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
        if python_spec.is_file():
            lines = [line.strip() for line in python_spec.read_text().splitlines() if line.strip() and not line.lstrip().startswith('#')]
            if not lines or any(not re.fullmatch(r'[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9_.+-]+', line) for line in lines):
                raise ValueError('Bundled Python specifications must pin exact package versions')
            if not self.uv.is_file() or hashlib.sha256(self.uv.read_bytes()).hexdigest() != UV_SHA256:
                raise ValueError('Verified bundled uv component is missing or modified; repair installation')
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
