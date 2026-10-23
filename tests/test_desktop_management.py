"""Actual validation and private management boundary tests."""
import json
import os
from pathlib import Path
import socket

import pytest

from desktop_runtime.config import DesktopConfig, protect, redact
from desktop_runtime.manager import Manager


def test_configuration_is_atomic_persistent_and_secret_free(tmp_path):
    config = DesktopConfig(tmp_path)
    config.save({'runtime_port': 19001, 'tunnel_id': 'tunnel_' + '0' * 32})
    assert DesktopConfig(tmp_path).public()['runtime_port'] == 19001
    before = config.path.read_bytes()
    with pytest.raises(ValueError):
        config.save({'runtime_port': True})
    assert config.path.read_bytes() == before
    assert 'secret' not in json.dumps(config.public())


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_dpapi_roundtrip_and_no_plaintext_key(tmp_path):
    secret = 'sk-test-desktop-private-value'
    config = DesktopConfig(tmp_path)
    config.set_secret(secret)
    assert secret.encode() not in config.secret_path.read_bytes()
    assert config.secret() == secret
    assert secret not in redact('key=' + secret, secret)


def test_management_rejects_general_execution_and_user_programs(tmp_path):
    manager = Manager(tmp_path / 'data', tmp_path / 'resources')
    for command in ('shell', 'run_process', 'exec', 'invoke_tool'):
        with pytest.raises(ValueError):
            manager.dispatch(command, {'program': 'cmd.exe'})
    with pytest.raises(ValueError):
        manager.dispatch('configure', {'runtime_command': 'cmd.exe'})


def test_port_conflict_is_refused_without_killing_listener(tmp_path):
    manager = Manager(tmp_path / 'data', tmp_path / 'resources')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        manager.config.save({'runtime_port': port})
        with pytest.raises(OSError):
            manager.start()
        assert listener.getsockname()[1] == port
        assert manager.children == {}


def test_workspace_permissions_reuse_policy_and_preserve_user_files(tmp_path):
    manager = Manager(tmp_path / 'data', tmp_path / 'resources')
    directory = tmp_path / 'project'
    directory.mkdir()
    marker = directory / 'keep.txt'
    marker.write_text('keep')
    result = manager.dispatch('workspace_save', {'name': 'project', 'path': str(directory),
                             'read': True, 'write': False, 'execute': False, 'expected_sha256': None})
    assert result['roots']['project']['write'] is False
    with pytest.raises(ValueError):
        manager.dispatch('workspace_save', {'name': 'private', 'path': str(manager.config.root),
                         'read': True, 'write': True, 'execute': True, 'expected_sha256': result['config_sha256']})
    manager.dispatch('workspace_remove', {'name': 'project', 'expected_sha256': result['config_sha256']})
    assert marker.read_text() == 'keep'


def test_multiple_roots_addition_never_overwrites_without_explicit_edit(tmp_path):
    manager = Manager(tmp_path / 'data', tmp_path / 'resources')
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir(); second.mkdir()
    fields = {'read': True, 'write': False, 'execute': False, 'mode': 'create'}
    one = manager.dispatch('workspace_save', {**fields, 'name': 'first', 'path': str(first), 'expected_sha256': None})
    two = manager.dispatch('workspace_save', {**fields, 'name': 'second', 'path': str(second), 'expected_sha256': one['config_sha256']})
    assert two['roots']['first']['path'] == str(first)
    assert two['roots']['second']['path'] == str(second)
    before = manager.config.workspace_path.read_bytes()
    with pytest.raises(ValueError, match='Root name already exists'):
        manager.dispatch('workspace_save', {**fields, 'name': 'first', 'path': str(second), 'expected_sha256': two['config_sha256']})
    assert manager.config.workspace_path.read_bytes() == before
    edited = manager.dispatch('workspace_save', {**fields, 'mode': 'update', 'name': 'first', 'path': str(second), 'expected_sha256': two['config_sha256']})
    assert edited['roots']['first']['path'] == str(second)
    assert edited['roots']['second'] == two['roots']['second']
    assert edited['config_sha256'] != two['config_sha256']
    with pytest.raises(ValueError, match='Root no longer exists'):
        manager.dispatch('workspace_save', {**fields, 'mode': 'update', 'name': 'missing', 'path': str(first), 'expected_sha256': edited['config_sha256']})


def test_developer_overrides_and_keys_are_not_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-not-for-desktop')
    monkeypatch.setenv('PLA_EXTERNAL_PROVIDERS', '*')
    monkeypatch.setenv('CONTROL_PLANE_API_KEY', 'sk-not-for-desktop')
    env = Manager(tmp_path / 'data', tmp_path / 'resources')._environment()
    assert 'OPENAI_API_KEY' not in env and 'CONTROL_PLANE_API_KEY' not in env
    assert env['PLA_EXTERNAL_PROVIDERS'] == ''
    assert Path(env['AGENT_WORKSPACE']).is_relative_to(tmp_path / 'data')


def test_runtime_cannot_register_private_desktop_data_or_mutate_installation(tmp_path, monkeypatch):
    from host.workspace_manager import build_root_policy, workspace_registry_upsert
    data, resources = tmp_path / 'data', tmp_path / 'resources'
    data.mkdir(); resources.mkdir()
    monkeypatch.setenv('PLA_DESKTOP_RUNTIME', '1')
    monkeypatch.setenv('PLA_DATA_ROOT', str(data))
    with pytest.raises(ValueError, match='private desktop data'):
        workspace_registry_upsert(data / 'config/roots.yaml', resources, name='private', path=str(data),
                                  read=True, write=True, execute=True, expected_sha256=None)
    policy = build_root_policy(data / 'workspace', resources)
    assert policy.resolve('pla', 'runtime/pla-runtime.exe', 'read').root.read
    for permission in ('write', 'execute'):
        with pytest.raises(ValueError, match='does not allow'):
            policy.resolve('pla', 'runtime/pla-runtime.exe', permission)


def test_legacy_detection_only_reads_layout_and_does_not_import_credentials(tmp_path):
    source = tmp_path / 'legacy'
    for name in ('src/server.py', 'start_all.ps1', 'stop_all.ps1', 'config/tunnel.local.yaml'):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('PRIVATE_EXISTING_INSTALLATION_SENTINEL', encoding='utf-8')
    manager = Manager(tmp_path / 'new-data', tmp_path / 'resources')
    result = manager.dispatch('detect_legacy', {'path': str(source)})
    assert result['recognized_source_install'] and result['tunnel_config_present']
    assert not result['imported'] and not result['processes_adopted']
    assert 'PRIVATE_EXISTING_INSTALLATION_SENTINEL' not in json.dumps(result)
    assert not manager.config.secret_path.exists()
    assert (source / 'config/tunnel.local.yaml').read_text() == 'PRIVATE_EXISTING_INSTALLATION_SENTINEL'


def test_tunnel_readiness_requires_fresh_remote_success_and_recovers_after_errors():
    from desktop_runtime.health import TunnelReadiness
    state = TunnelReadiness()
    def metrics(success, errors=0):
        return f'commands_poll_last_successful_timestamp_seconds {success}\ncommands_poll_errors_total{{error_kind="other"}} {errors}\n'
    assert state.observe(True, metrics(0), 100) == 'local_ready_waiting_control_plane'
    assert state.observe(True, metrics(100), 101) == 'ready'
    assert state.observe(True, metrics(100, 1), 102) == 'disconnected'
    assert state.observe(True, metrics(103, 1), 104) == 'ready'
    assert state.observe(True, metrics(103, 1), 170) == 'disconnected'
    assert state.observe(False, metrics(171, 1), 172) == 'starting_or_disconnected'
    assert TunnelReadiness().observe(True, 'unknown_metric 100\n', 100) != 'ready'


def test_tunnel_cannot_inherit_developer_profiles_or_unsafe_logging(tmp_path, monkeypatch):
    for name in ('TUNNEL_CLIENT_CONFIG', 'TUNNEL_CLIENT_PROFILE', 'LOG_HTTP_RAW_UNSAFE', 'OPENAI_API_KEY', 'CONTROL_PLANE_API_KEY'):
        monkeypatch.setenv(name, 'existing-private-value')
    env = Manager(tmp_path / 'data', tmp_path / 'resources')._tunnel_environment()
    assert not any(name in env for name in ('TUNNEL_CLIENT_CONFIG', 'TUNNEL_CLIENT_PROFILE', 'LOG_HTTP_RAW_UNSAFE', 'OPENAI_API_KEY', 'CONTROL_PLANE_API_KEY'))
    assert 'SYSTEMROOT' in env if os.name == 'nt' else True
