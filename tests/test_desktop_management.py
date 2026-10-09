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
    config.save({'runtime_port': 19001, 'tunnel_id': 'tunnel_test'})
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


def test_developer_overrides_and_keys_are_not_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-not-for-desktop')
    monkeypatch.setenv('PLA_EXTERNAL_PROVIDERS', '*')
    monkeypatch.setenv('CONTROL_PLANE_API_KEY', 'sk-not-for-desktop')
    env = Manager(tmp_path / 'data', tmp_path / 'resources')._environment()
    assert 'OPENAI_API_KEY' not in env and 'CONTROL_PLANE_API_KEY' not in env
    assert env['PLA_EXTERNAL_PROVIDERS'] == ''
    assert Path(env['AGENT_WORKSPACE']).is_relative_to(tmp_path / 'data')
