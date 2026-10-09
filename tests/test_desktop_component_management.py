import json
import socket
import sys

import pytest

from desktop_runtime.manager import Manager
from desktop_runtime.capability_bridge import checked_provider_action, checked_skill_action, SKILL_ACTIONS


def test_fixed_bridge_cannot_invoke_arbitrary_shell_or_skip_confirmation():
    for action in ('shell', 'run_process', 'setup', 'status'):
        with pytest.raises(ValueError):
            checked_provider_action({'action': action, 'provider_id': 'demo', 'confirmed': True})
    with pytest.raises(ValueError):
        checked_provider_action({'action': 'enable', 'provider_id': 'demo', 'confirmed': False})
    assert len(SKILL_ACTIONS) == 17
    for action in SKILL_ACTIONS:
        capability, arguments = checked_skill_action({'action': action, 'arguments': {}, 'confirmed': False})
        assert capability == 'skill-library.' + action
    with pytest.raises(ValueError):
        checked_skill_action({'action': 'runtime.run_process', 'arguments': {}, 'confirmed': True})


def test_skill_permissions_never_exceed_independent_workspace_grants(tmp_path):
    manager = Manager(tmp_path / 'data', tmp_path / 'resources')
    folder = tmp_path / 'skills'
    folder.mkdir()
    registry = manager.dispatch('workspace_save', {'mode': 'create', 'name': 'skills', 'path': str(folder),
        'read': True, 'write': False, 'execute': False, 'expected_sha256': None})
    before = manager.dispatch('skill_permissions', {})
    assert before['local_roots'] == before['writable_roots'] == []
    with pytest.raises(ValueError, match='cannot exceed'):
        manager.dispatch('skill_permissions', {'local_roots': ['skills'], 'writable_roots': ['skills'], 'confirmed': True})
    saved = manager.dispatch('skill_permissions', {'local_roots': ['skills'], 'writable_roots': [], 'confirmed': True})
    assert saved['local_roots'] == ['skills'] and saved['writable_roots'] == []
    assert manager.dispatch('skill_permissions', {})['local_roots'] == ['skills']
    assert manager.config.workspace_path.exists()


def test_development_default_off_enable_revoke_preserves_source_and_other_roots(tmp_path):
    manager = Manager(tmp_path / 'data', tmp_path / 'resources')
    source = tmp_path / 'source'
    for name in ('src/server.py', 'desktop/packaging/build.ps1', 'desktop/src-tauri/tauri.conf.json'):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('source sentinel')
    before = manager.dispatch('development_status', {})
    assert not before['enabled'] and before['installed_resources_read_only']
    result = manager.dispatch('development_configure', {'enabled': True, 'path': str(source), 'confirmed': True, 'expected_sha256': before['config_sha256']})
    assert manager.dispatch('development_status', {})['enabled']
    with pytest.raises(ValueError):
        manager.dispatch('development_configure', {'enabled': False, 'path': '', 'confirmed': True, 'expected_sha256': before['config_sha256']})
    manager.dispatch('development_configure', {'enabled': False, 'path': '', 'confirmed': True, 'expected_sha256': result['config_sha256']})
    assert not manager.dispatch('development_status', {})['enabled']
    assert (source / 'src/server.py').read_text() == 'source sentinel'


def test_real_mcp_plugin_import_enable_reload_disable_without_runtime_restart(tmp_path):
    manager = Manager(tmp_path / 'data', tmp_path / 'resources')
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        port = connection.getsockname()[1]
    manager.config.save({'runtime_port': port})
    script = tmp_path / 'provider.py'
    script.write_text('from fastmcp import FastMCP\nmcp=FastMCP("desktop-real-hotplug")\n@mcp.tool\ndef read_note() -> dict:\n    return {"proof":"real-desktop-provider"}\nmcp.run()\n')
    content = json.dumps({'schema_version': 1, 'id': 'test-mcp', 'runtime': {
        'kind': 'executable_stdio', 'command': sys.executable, 'args': [str(script)], 'cwd': '.'}, 'tool_allowlist': ['read_note'],
        'tool_overrides': {'read_note': {'risk_level': 'read', 'requires_confirmation': False}}})
    try:
        manager.start()
        runtime_pid = manager.children['runtime'].pid
        preview = manager.dispatch('provider_import', {'content': content, 'confirmed': False, 'expected_sha256': None})
        manager.dispatch('provider_import', {'content': content, 'confirmed': True, 'expected_sha256': preview['sha256']})
        assert manager.dispatch('provider_catalog', {})['providers'][0]['requested_enabled'] is False
        result = manager.dispatch('provider_action', {'action': 'enable', 'provider_id': 'test-mcp', 'confirmed': True})
        assert result['data']['provider']['state'] == 'ready'
        tools = manager.dispatch('provider_details', {'provider_id': 'test-mcp'})
        assert tools['capabilities'][0]['id'] == 'test-mcp.read_note'
        assert tools['capabilities'][0]['available']
        result = manager._invoke_management_capability('test-mcp.read_note', {})
        assert result['data'] == {'proof': 'real-desktop-provider'}
        second_content = content.replace('test-mcp', 'second-mcp')
        preview = manager._invoke_management_capability('runtime.provider_import', {'content': second_content}, True)['data']
        assert not preview['imported'] and not preview['launched']
        registered = manager._invoke_management_capability('runtime.provider_import', {'content': second_content, 'confirm': True, 'expected_sha256': preview['sha256']}, True)['data']
        assert registered['imported'] and not registered['launched']
        assert {item['provider_id'] for item in manager.dispatch('provider_catalog', {})['providers']} == {'test-mcp', 'second-mcp'}
        manager.dispatch('provider_action', {'action': 'reload', 'provider_id': 'test-mcp', 'confirmed': True})
        manager.dispatch('provider_action', {'action': 'disable', 'provider_id': 'test-mcp', 'confirmed': True})
        assert not manager.dispatch('provider_details', {'provider_id': 'test-mcp'})['capabilities'][0]['available']
        assert manager.children['runtime'].pid == runtime_pid
        assert manager.components.preferences()['enabled'] == []
        manager.stop()
        pending = manager.dispatch('provider_action', {'action': 'enable', 'provider_id': 'test-mcp', 'confirmed': True})
        assert pending['status'] == 'selection_saved' and pending['connected'] is False
        manager.start()
        assert manager.dispatch('provider_details', {'provider_id': 'test-mcp'})['capabilities'][0]['available']
    finally:
        manager.stop()
