"""Real isolated Desktop optional component installation and Skill MCP acceptance.

Uses no Tunnel credential, existing provider environment or user Skill source.
This source-mode check is separate from frozen and native UI acceptance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from queue import Queue
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from desktop_runtime.manager import Manager


class FrozenManager:
    """Same bounded private management RPC as Tauri; no developer Runtime."""
    def __init__(self, data, resources):
        self.components = SimpleNamespace(root=data / 'components')
        self.process = subprocess.Popen([str(resources / 'runtime/pla-runtime.exe'), 'manager',
            '--data-dir', str(data), '--resources', str(resources.resolve())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.responses = Queue()
        def collect():
            for line in self.process.stdout:
                self.responses.put(line)
            self.responses.put(None)
        Thread(target=collect, daemon=True).start()

    def dispatch(self, command, args=None):
        self.process.stdin.write(json.dumps({'command': command, 'args': args or {}}) + '\n')
        self.process.stdin.flush()
        raw = self.responses.get(timeout=100)
        if not raw:
            raise RuntimeError('Frozen manager exited without a response')
        result = json.loads(raw)
        if not result['ok']:
            raise RuntimeError(result['error'])
        return result['result']

    def start(self):
        return self.dispatch('start')

    def stop(self):
        try:
            self.dispatch('stop')
        finally:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                subprocess.run(['taskkill.exe', '/PID', str(self.process.pid), '/T', '/F'], capture_output=True)
                self.process.wait(timeout=10)

    @property
    def logs(self):
        return self.dispatch('logs')['lines']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--skill-package', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--frozen', action='store_true')
    args = parser.parse_args()
    data = Path(tempfile.mkdtemp(prefix='pla-component-mcp-'))
    manager = FrozenManager(data, args.resources) if args.frozen else Manager(data, args.resources)
    checks = []
    def record(name, evidence):
        checks.append({'name': name, 'result': 'PASS', 'evidence': evidence})
        print('PASS ' + name, flush=True)
    def skill(action, arguments=None, confirmed=False):
        value = manager.dispatch('skill_action', {'action': action, 'arguments': arguments or {}, 'confirmed': confirmed})
        assert value.get('status') == 'completed' and not value.get('is_error'), value
        result = value['data']
        assert result.get('status') not in {'error', 'failed', 'blocked'}, result
        return result
    try:
        catalog = manager.dispatch('provider_catalog', {})
        assert len(catalog['providers']) == 11 and not catalog['runtime_observed']
        assert all(not item['requested_enabled'] for item in catalog['providers'])
        record('all_builtin_manifests_visible_without_borrowed_dependencies', len(catalog['providers']))
        install_args = {'provider_id': 'skill-library', 'skill_package': str(args.skill_package.resolve()), 'confirmed': False, 'expected_sha256': None}
        plan = manager.dispatch('provider_install', install_args)
        manager.dispatch('provider_install', {**install_args, 'confirmed': True, 'expected_sha256': plan['sha256']})
        deadline = time.monotonic() + 600
        job = 'installer:skill-library'
        def job_status():
            return next(item for item in manager.dispatch('installation_status', {})['jobs'] if item['job'] == job)
        while job_status()['state'] == 'running' and time.monotonic() < deadline:
            time.sleep(1)
        assert job_status()['exit_code'] == 0, '\n'.join(manager.logs)[-16000:]
        python = manager.components.root / '.provider_envs/skill-library/Scripts/python.exe'
        assert python.is_file()
        record('independent_managed_python_and_skill_package_install', str(python))
        sources = data / 'workspace/skills-source'
        sources.mkdir()
        content = '---\nname: desktop-test\ndescription: Real Desktop Skill acceptance test\n---\n\n# Desktop Test\nRead a fixture safely.\n'
        folder = sources / 'skills/desktop-test'
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text(content, encoding='utf-8')
        (folder / 'references').mkdir()
        (folder / 'references/example.txt').write_text('REAL_SKILL_RESOURCE', encoding='utf-8')
        manager.dispatch('skill_permissions', {'local_roots': ['workspace'], 'writable_roots': ['workspace'], 'confirmed': True})
        with socket.socket() as connection:
            connection.bind(('127.0.0.1', 0))
            port = connection.getsockname()[1]
        manager.dispatch('configure', {'runtime_port': port})
        manager.start()
        runtime_pid = manager.dispatch('status', {})['owned_processes']['runtime']['pid']
        enabled = manager.dispatch('provider_action', {'action': 'enable', 'provider_id': 'skill-library', 'confirmed': True})
        assert enabled['data']['provider']['state'] == 'ready', enabled
        descriptors = manager.dispatch('provider_details', {'provider_id': 'skill-library'})
        assert len(descriptors['capabilities']) == 17
        record('skill_library_17_real_descriptors_and_schemas', len(descriptors['capabilities']))
        skill('manage', {'action': 'add', 'source_id': 'test', 'kind': 'local', 'location': str(sources)})
        skill('sources')
        synchronized = skill('sync', {'source_id': 'test'})
        assert synchronized['results']['test']['status'] not in {'error', 'failed'}, synchronized
        skill('find', {'query': 'Desktop', 'include_best_content': True})
        loaded = skill('load', {'name': 'test:desktop-test'})
        assert loaded['content'] == content
        resource = skill('resource', {'name': 'test:desktop-test', 'path': 'references/example.txt'})
        assert 'REAL_SKILL_RESOURCE' in str(resource)
        skill('validate', {'name': 'test:desktop-test'})
        skill('states')
        skill('toggle', {'source_id': 'test', 'name': 'desktop-test', 'enabled': False})
        try:
            skill('load', {'name': 'test:desktop-test'})
            raise AssertionError('Disabled Skill unexpectedly readable')
        except Exception as error:
            assert 'disabled' in str(error).lower(), error
        skill('toggle', {'source_id': 'test', 'name': 'desktop-test', 'enabled': True})
        record('source_search_read_resource_validate_and_individual_toggle', 'actual MCP calls')
        draft = skill('prepare', {'source_id': 'test', 'name': 'desktop-test', 'content': content + '\nReviewed edit.\n'})
        skill('apply-local', {'draft_id': draft['draft_id'], 'expected_current_sha256': draft['expected_current_sha256'], 'confirm': True}, True)
        assert 'Reviewed edit.' in (folder / 'SKILL.md').read_text()
        plan = skill('plan-change', {'action': 'rename', 'source_id': 'test', 'name': 'desktop-test', 'new_name': 'desktop-renamed'})
        skill('apply-change', {'plan_id': plan['plan_id'], 'expected_tree_sha256': plan['expected_tree_sha256'], 'confirm': True}, True)
        renamed = sources / 'skills/desktop-renamed'
        assert renamed.is_dir() and not folder.exists()
        plan = skill('plan-change', {'action': 'delete', 'source_id': 'test', 'name': 'desktop-renamed'})
        deleted = skill('apply-change', {'plan_id': plan['plan_id'], 'expected_tree_sha256': plan['expected_tree_sha256'], 'confirm': True}, True)
        assert not renamed.exists()
        skill('restore-local', {'trash_id': deleted['trash_id'], 'confirm': True}, True)
        assert renamed.is_dir()
        record('reviewed_draft_apply_rename_archive_restore', 'actual files independently inspected')
        large_content = '---\nname: desktop-large\ndescription: Unicode document boundary fixture\n---\n\n' + '验收正文' * 5000
        large = skill('prepare', {'source_id': 'test', 'name': 'desktop-large', 'content': large_content})
        assert large['content'] == large_content
        record('bounded_rpc_preserves_large_unicode_skill_draft', len(large_content.encode('utf-8')))
        skill('publish-plan', {'source_id': 'test', 'github_repo': 'desktop-test/fixture', 'visibility': 'private'})
        git = shutil.which('git.exe') or shutil.which('git')
        assert git, 'Git required for publication fixture; this is an optional system prerequisite'
        destination = data / 'workspace/destination'
        destination.mkdir()
        subprocess.run([git, 'init', '-q', str(destination)], check=True)
        skill('manage', {'action': 'add', 'source_id': 'destination', 'kind': 'local', 'location': str(destination)})
        plan = skill('publication-prepare', {'source_id': 'test', 'destination_source_id': 'destination'})
        skill('publication-apply', {'plan_id': plan['plan_id'], 'expected_tree_sha256': plan['expected_tree_sha256'], 'confirm': True}, True)
        assert (destination / 'skills/desktop-renamed/SKILL.md').read_bytes() == (renamed / 'SKILL.md').read_bytes()
        record('publication_preview_and_local_copy_no_commit_or_push', 'destination bytes independently compared')
        manager.dispatch('provider_action', {'action': 'disable', 'provider_id': 'skill-library', 'confirmed': True})
        assert manager.dispatch('status', {})['owned_processes']['runtime']['pid'] == runtime_pid
        record('runtime_pid_preserved_across_skill_enable_disable', runtime_pid)
    except Exception as error:
        checks.append({'name': 'acceptance', 'result': 'FAIL', 'error': str(error)})
        raise
    finally:
        manager.stop()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({'mode': 'frozen_manager_real_independent_component' if args.frozen else 'source_manager_real_independent_component', 'data_directory': str(data), 'checks': checks}, indent=2, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
