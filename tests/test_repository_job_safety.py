"""Repository review/ownership regression tests; dependency execution is stubbed."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from desktop_runtime.components import ComponentProject
from desktop_runtime.manager import Manager
from desktop_runtime.repository_jobs import prepare, load, plan_path
from provider.provider_repository import RepositoryClient, checked_relative_path


REPOSITORY = Path(__file__).resolve().parents[1] / 'test-provider-repository'


@pytest.fixture
def project(tmp_path):
    return ComponentProject(tmp_path/'data',tmp_path/'resources')


@pytest.fixture
def package():
    return RepositoryClient(REPOSITORY.as_uri()).download_package('test-echo-provider','1.0.0')


def versioned(package, version):
    spec = {**package.spec,'version':version}
    metadata = {**package.metadata_bytes,'spec.json':json.dumps(spec,indent=2,sort_keys=True).encode()}
    checksums = {**package.checksums,'files':{**package.checksums['files'],
        'spec.json':'sha256:'+hashlib.sha256(metadata['spec.json']).hexdigest()}}
    return replace(package,version=version,spec=spec,metadata_bytes=metadata,checksums=checksums)


def with_manifest(package, manifest):
    raw=json.dumps(manifest,indent=2,sort_keys=True).encode()
    return replace(package,manifest=manifest,
        metadata_bytes={**package.metadata_bytes,'manifest.json':raw},
        checksums={**package.checksums,'files':{**package.checksums['files'],
            'manifest.json':'sha256:'+hashlib.sha256(raw).hexdigest()}})


def fake_dependencies(lifecycle, monkeypatch):
    def install(package, env):
        (env/'Scripts').mkdir(parents=True)
        (env/'Scripts/python.exe').write_bytes(b'test placeholder, never executed')
    monkeypatch.setattr(lifecycle,'_install_dependencies',install)


@pytest.mark.parametrize('path',['../outside','assets/../x','assets/CON.txt','assets/a.','assets/a:b','assets/COM1','assets/a\\b'])
def test_windows_asset_boundaries(path):
    with pytest.raises(ValueError): checked_relative_path(path)


def test_raw_metadata_and_case_alias_rejected(package):
    with pytest.raises(ValueError,match='metadata'):
        replace(package,spec={**package.spec,'dependencies':{}}).verify_checksums()
    files = {**package.checksums['files'],'ASSETS/PROVIDERS/TEST_ECHO_PROVIDER.PY':'sha256:'+'0'*64}
    with pytest.raises(ValueError,match='ambiguous'):
        replace(package,checksums={**package.checksums,'files':files}).verify_checksums()


def test_plan_binds_complete_download_and_requested_arguments(project):
    preview = prepare(project,'install','test-echo-provider',REPOSITORY.as_uri())
    payload = load(project,preview['sha256'],'install','test-echo-provider',REPOSITORY.as_uri(),None)
    assert payload['package']['assets']
    assert preview['files']['manifest.json'].startswith('sha256:')
    with pytest.raises(ValueError,match='match'):
        load(project,preview['sha256'],'upgrade','test-echo-provider',REPOSITORY.as_uri(),None)
    path = plan_path(project,preview['sha256'])
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError,match='modified'): load(project,preview['sha256'])


def test_failed_upgrade_keeps_previous_installation_and_retry_target(project,package,monkeypatch):
    lifecycle = project._lifecycle_manager
    fake_dependencies(lifecycle,monkeypatch)
    original = lifecycle.install_provider(package,REPOSITORY.as_uri())
    manifest = lifecycle._manifest_path(package.provider_id).read_bytes()
    states = lifecycle.state_file.read_bytes()
    def fail(message,percent):
        if percent == 95: raise RuntimeError('commit failure')
    upgraded = versioned(package,'1.0.1')
    with pytest.raises(RuntimeError,match='commit failure'):
        lifecycle.upgrade_provider(package.provider_id,upgraded,REPOSITORY.as_uri(),fail)
    assert lifecycle._manifest_path(package.provider_id).read_bytes() == manifest
    assert lifecycle.state_file.read_bytes() == states
    assert Path(original.environment_path).is_dir()
    env,namespace=lifecycle._paths(package.provider_id,'1.0.1')
    assert not env.exists() and not namespace.exists()
    lifecycle._verify_record(original)


def test_uninstall_preserves_changed_and_unrelated_files(project,package,monkeypatch):
    lifecycle = project._lifecycle_manager
    fake_dependencies(lifecycle,monkeypatch)
    record = lifecycle.install_provider(package,REPOSITORY.as_uri())
    _,namespace=lifecycle._paths(package.provider_id,package.version)
    extra=namespace/'user-notes.txt';extra.write_text('preserve')
    asset=next(iter(lifecycle._asset_paths(record)))
    original=asset.read_bytes();asset.write_bytes(b'user changed')
    with pytest.raises(ValueError,match='asset changed'): lifecycle.uninstall_provider(package.provider_id)
    assert asset.read_bytes()==b'user changed'
    assert lifecycle.get_provider_state(package.provider_id) is not None
    asset.write_bytes(original)
    lifecycle.uninstall_provider(package.provider_id)
    assert extra.read_text()=='preserve'
    assert not Path(record.environment_path).exists()


def test_uninstall_failure_restores_manifest_assets_environment_and_state(project,package,monkeypatch):
    lifecycle=project._lifecycle_manager
    fake_dependencies(lifecycle,monkeypatch)
    record=lifecycle.install_provider(package,REPOSITORY.as_uri())
    state=lifecycle.state_file.read_bytes()
    def fail(*args): raise RuntimeError('injected post-uninstall failure')
    lifecycle.register_hook('post_uninstall',fail)
    with pytest.raises(RuntimeError,match='post-uninstall'):
        lifecycle.uninstall_provider(package.provider_id)
    lifecycle._verify_record(record)
    assert Path(record.environment_path).is_dir()
    assert lifecycle.state_file.read_bytes()==state


def test_upgrade_retains_ownership_and_uninstall_removes_all_owned_versions(project,package,monkeypatch):
    lifecycle=project._lifecycle_manager
    fake_dependencies(lifecycle,monkeypatch)
    previous=lifecycle.install_provider(package,REPOSITORY.as_uri())
    upgraded=lifecycle.upgrade_provider(package.provider_id,versioned(package,'1.0.1'),REPOSITORY.as_uri())
    lifecycle._verify_record(upgraded)
    assert len(upgraded.metadata['previous_versions'])==1
    lifecycle.uninstall_provider(package.provider_id)
    assert not Path(previous.environment_path).exists()
    assert not Path(upgraded.environment_path).exists()
    assert lifecycle.list_installed_providers()==[]
    assert not lifecycle._paths(package.provider_id, package.version)[1].exists()
    assert not lifecycle._paths(package.provider_id, upgraded.version)[1].exists()
    reinstalled=lifecycle.install_provider(package,REPOSITORY.as_uri())
    lifecycle._verify_record(reinstalled)


def test_redirected_version_directory_is_rejected_and_foreign_files_preserved(project,package,monkeypatch):
    import os
    import subprocess
    lifecycle=project._lifecycle_manager
    _,namespace=lifecycle._paths(package.provider_id, package.version)
    namespace.parent.mkdir(parents=True)
    foreign=project.root/'foreign-data'
    foreign.mkdir();marker=foreign/'user-notes.txt';marker.write_text('preserve')
    if os.name=='nt':
        result=subprocess.run(['cmd.exe','/c','mklink','/J',str(namespace),str(foreign)],capture_output=True)
        assert result.returncode==0,result.stderr
    else:
        namespace.symlink_to(foreign,target_is_directory=True)
    monkeypatch.setattr(lifecycle,'_install_dependencies',lambda *args:pytest.fail('No dependency execution is allowed'))
    with pytest.raises(ValueError,match='redirect'):
        lifecycle.install_provider(package,REPOSITORY.as_uri())
    assert marker.read_text()=='preserve'


def test_distinct_empty_working_directory_is_removed_for_reinstall(project,package,monkeypatch):
    lifecycle=project._lifecycle_manager
    fake_dependencies(lifecycle,monkeypatch)
    manifest={**package.manifest,'runtime':{**package.manifest['runtime'],
        'args':['-c','pass'],'cwd':'runtime/empty'}}
    package=with_manifest(package,manifest)
    first=lifecycle.install_provider(package,REPOSITORY.as_uri())
    assert first.metadata['created_directories']
    lifecycle.uninstall_provider(package.provider_id)
    lifecycle.install_provider(package,REPOSITORY.as_uri())


def test_failed_install_removes_its_empty_working_directory(project,package,monkeypatch):
    lifecycle=project._lifecycle_manager
    fake_dependencies(lifecycle,monkeypatch)
    package=with_manifest(package,{**package.manifest,'runtime':{**package.manifest['runtime'],
        'args':['-c','pass'],'cwd':'runtime/empty'}})
    def fail(*args): raise RuntimeError('post-install failure')
    lifecycle.register_hook('post_install',fail)
    with pytest.raises(RuntimeError,match='post-install'):
        lifecycle.install_provider(package,REPOSITORY.as_uri())
    env,namespace=lifecycle._paths(package.provider_id,package.version)
    assert not env.exists() and not namespace.exists()


def test_confirmed_manager_spawns_fixed_worker_without_sync_install(tmp_path,monkeypatch):
    manager=Manager(tmp_path/'data',tmp_path/'resources')
    args={'provider_id':'test-echo-provider','repository_url':REPOSITORY.as_uri(),
          'version':None,'confirmed':False,'expected_sha256':None}
    preview=manager.dispatch('provider_install_remote',args)
    calls=[]
    monkeypatch.setattr(manager,'_spawn_installer',lambda role,command:calls.append((role,command)))
    monkeypatch.setattr(manager.components,'install_provider_from_repository',lambda **kwargs:pytest.fail('Synchronous install'))
    with pytest.raises(ValueError):
        manager.dispatch('provider_install_remote',{**args,'confirmed':True,'expected_sha256':'0'*64})
    assert calls==[]
    result=manager.dispatch('provider_install_remote',{**args,'confirmed':True,'expected_sha256':preview['sha256']})
    assert result['started'] and result['job']=='installer:repository-test-echo-provider'
    assert len(calls)==1 and 'repository_job' in calls[0][1]
    assert calls[0][1][-1]==preview['sha256']


def test_catalog_includes_repository_version_and_missing_manifest(tmp_path, package, monkeypatch):
    manager = Manager(tmp_path/'data', tmp_path/'resources')
    lifecycle = manager.components._lifecycle_manager
    fake_dependencies(lifecycle, monkeypatch)
    lifecycle.install_provider(package, REPOSITORY.as_uri())
    row = next(row for row in manager.dispatch('provider_catalog', {})['providers']
               if row['provider_id'] == package.provider_id)
    assert row['repository_installation']['version'] == package.version
    assert row['installation_recorded'] is True
    assert row['repository_installation']['repository_url'] == REPOSITORY.as_uri()
    (manager.components.manifest_dir/f'{package.provider_id}.json').unlink()
    missing = next(row for row in manager.dispatch('provider_catalog', {})['providers']
                   if row['provider_id'] == package.provider_id)
    assert missing['manifest_missing'] is True
    assert missing['execution_files_present'] is False
    assert missing['repository_installation']['version'] == package.version
