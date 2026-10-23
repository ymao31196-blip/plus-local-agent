"""Immutable reviewed repository plans and a fixed owned subprocess role."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re

from desktop_runtime.config import atomic_write
from desktop_runtime.components import ComponentProject
from provider.provider_repository import ProviderPackage, RepositoryClient, checked_identity


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def plan_path(project, digest):
    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('Review the full installation plan before confirming')
    target = project.root / 'repository-plans' / (digest + '.json')
    if not target.resolve().is_relative_to(project.root):
        raise ValueError('Plan path escapes private components')
    return target


def installed_snapshot(project, provider_id):
    lifecycle = project._lifecycle_manager
    record = lifecycle.get_provider_state(provider_id)
    if record:
        lifecycle._verify_record(record)
    elif lifecycle._manifest_path(provider_id).exists():
        raise ValueError('Existing unowned provider manifest preserved')
    return record.to_dict() if record else None


def prepare(project, operation, provider_id, repository_url=None, version=None):
    checked_identity(provider_id, version)
    current = installed_snapshot(project, provider_id)
    if operation == 'install' and current:
        raise ValueError('Provider already installed')
    if operation in {'upgrade', 'uninstall'} and not current:
        raise ValueError('Provider is not installed')
    payload = {'schema_version': 1, 'operation': operation, 'provider_id': provider_id,
               'repository_url': repository_url, 'requested_version': version,
               'current': current, 'package': None}
    public = {'operation': operation, 'provider_id': provider_id, 'current': current,
              'repository_url': repository_url, 'activates_provider': False}
    if operation != 'uninstall':
        client = RepositoryClient(repository_url, project.root / 'repository-cache')
        if version is None:
            versions = client.list_versions(provider_id)
            if not versions:
                raise ValueError('No repository version is available')
            version = versions[0].version
        package = client.download_package(provider_id, version)
        if current and current['version'] == version:
            raise ValueError('Select a different version for upgrade')
        env, namespace = project._lifecycle_manager._paths(provider_id, version)
        if env.exists() or namespace.exists():
            raise ValueError('Version directory already exists; existing files preserved')
        deployed = project._lifecycle_manager._prepare_manifest(package, env, namespace)
        payload['package'] = {'provider_id': provider_id, 'version': version,
            'spec': package.spec, 'manifest': package.manifest, 'checksums': package.checksums,
            'assets': {k: base64.b64encode(v).decode() for k,v in package.assets.items()},
            'metadata_bytes': {k: base64.b64encode(v).decode() for k,v in package.metadata_bytes.items()}}
        public.update(version=version, target_version=version, spec=package.spec,
                      manifest=deployed, files=package.checksums['files'],
                      download_bytes=sum(map(len,package.assets.values())), environment=str(env))
    raw = encoded(payload)
    digest = hashlib.sha256(raw).hexdigest()
    atomic_write(plan_path(project,digest), raw)
    return {**public, 'sha256':digest, 'started':False}


def load(project, digest, operation=None, provider_id=None, repository_url=None, version=None):
    path = plan_path(project,digest)
    if not path.is_file() or path.stat().st_size > 65_000_000:
        raise ValueError('Reviewed plan is missing or exceeds size limit')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Reviewed plan was modified')
    payload = json.loads(raw)
    if payload.get('schema_version') != 1 or payload.get('operation') not in {'install','upgrade','uninstall'}:
        raise ValueError('Invalid repository plan')
    checked_identity(payload['provider_id'])
    if operation is not None and (payload['operation'],payload['provider_id'],payload['repository_url'],payload['requested_version']) != (operation,provider_id,repository_url,version):
        raise ValueError('Confirmation does not match reviewed plan')
    if installed_snapshot(project,payload['provider_id']) != payload['current']:
        raise ValueError('Installed provider changed after preview; review again')
    return payload


def execute(project, digest):
    payload = load(project,digest)
    operation, provider_id = payload['operation'],payload['provider_id']
    report = lambda message, percent: print(f'STEP {percent}% {message}',flush=True)
    lifecycle = project._lifecycle_manager
    if operation == 'uninstall':
        lifecycle.uninstall_provider(provider_id,report)
        result = {'provider_id':provider_id,'success':True,'operation':operation}
    else:
        value = dict(payload['package'])
        for field in ('assets','metadata_bytes'):
            value[field] = {k:base64.b64decode(v,validate=True) for k,v in value[field].items()}
        package = ProviderPackage(**value)
        package.verify_checksums()
        if package.manifest['runtime']['kind'] == 'isolated_python_stdio':
            from desktop_runtime.installer import ComponentInstaller
            ComponentInstaller(project.data,project.resources).ensure_uv()
        # Recheck after dependency bootstrap, before any provider mutation.
        load(project,digest)
        if operation == 'install':
            record = lifecycle.install_provider(package,payload['repository_url'],report)
        else:
            record = lifecycle.upgrade_provider(provider_id,package,payload['repository_url'],report)
        result = {**record.to_dict(),'success':True,'operation':operation}
    receipt = project.root / 'receipts' / ('repository-' + provider_id + '.json')
    if not receipt.resolve().is_relative_to(project.root):
        raise ValueError('Receipt escapes private components')
    atomic_write(receipt,encoded({'sha256':digest,'result':result}))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir',required=True)
    parser.add_argument('--resources',required=True)
    parser.add_argument('--expected-plan-sha256',required=True)
    args = parser.parse_args()
    project = ComponentProject(Path(args.data_dir),Path(args.resources))
    print(json.dumps(execute(project,args.expected_plan_sha256)),flush=True)
