import json

import pytest

from desktop_runtime.components import ComponentProject


def template(resources, provider_id='demo'):
    folder = resources / 'provider-assets/provider_manifests'
    folder.mkdir(parents=True, exist_ok=True)
    value = {'schema_version': 1, 'id': provider_id, 'autostart': True, 'mode': 'auto',
             'runtime': {'kind': 'isolated_python_stdio', 'python': f'.provider_envs/{provider_id}/Scripts/python.exe',
                         'args': ['-m', 'demo_server'], 'cwd': '.'}}
    (folder / f'{provider_id}.json').write_text(json.dumps(value))


def test_components_show_missing_providers_without_enabling_or_borrowing_environments(tmp_path):
    resources = tmp_path / 'installed'
    template(resources)
    project = ComponentProject(tmp_path / 'data', resources)
    catalog = project.stage(18931)
    row = catalog['providers'][0]
    assert row['provider_id'] == 'demo'
    assert row['python_exists'] is False
    assert row['requested_enabled'] is False
    assert str(project.root) in row['python']
    assert project.select('demo', True) == {'enabled': ['demo']}
    assert ComponentProject(tmp_path / 'data', resources).preferences() == {'enabled': ['demo']}
    project.stage(18932)
    assert project.catalog()['providers'][0]['requested_enabled'] is True


def test_component_staging_keeps_private_configs_and_user_manifests(tmp_path):
    resources = tmp_path / 'installed'
    template(resources)
    providers = resources / 'provider-assets/providers'
    providers.mkdir()
    (providers / 'adapter.py').write_text('reviewed')
    (providers / 'credentials.local.json').write_text('DO_NOT_COPY')
    project = ComponentProject(tmp_path / 'data', resources)
    project.stage(18931)
    assert (project.root / 'providers/adapter.py').read_text() == 'reviewed'
    assert not (project.root / 'providers/credentials.local.json').exists()
    custom = project.manifest_dir / 'custom.json'
    custom.write_text(json.dumps({'schema_version': 1, 'id': 'custom', 'runtime': {
        'kind': 'streamable_http', 'url': 'http://127.0.0.1:19000/mcp', 'cwd': '.'}}))
    project.stage(18931)
    assert {row['provider_id'] for row in project.catalog()['providers']} == {'custom', 'demo'}
    assert custom.exists()
    with pytest.raises(ValueError, match='Unknown provider'):
        project.select('missing', True)
    with pytest.raises(ValueError, match='Invalid provider selection'):
        project.select('../escape', True)


def test_import_requires_exact_review_and_never_launches(tmp_path):
    project = ComponentProject(tmp_path / 'data', tmp_path / 'resources')
    content = json.dumps({'schema_version': 1, 'id': 'custom', 'runtime': {
        'kind': 'streamable_http', 'url': 'http://127.0.0.1:19000/mcp', 'cwd': '.'},
        'tool_allowlist': ['read_note']})
    preview = project.import_manifest(content)
    assert not preview['imported'] and not preview['launched']
    assert project.catalog()['providers'] == []
    with pytest.raises(ValueError, match='exact manifest'):
        project.import_manifest(content, confirm=True, expected_sha256='wrong')
    result = project.import_manifest(content, confirm=True, expected_sha256=preview['sha256'])
    assert result['imported'] and not result['launched']
    assert project.catalog()['providers'][0]['requested_enabled'] is False
    with pytest.raises(ValueError, match='already exists'):
        project.import_manifest(content)


def test_staging_preserves_user_changes_to_existing_manifest(tmp_path):
    resources = tmp_path / 'resources'
    template(resources)
    project = ComponentProject(tmp_path / 'data', resources)
    project.stage(18931)
    path = project.manifest_dir / 'demo.json'
    value = json.loads(path.read_text())
    value['runtime']['args'] = ['-m', 'updated_server']
    path.write_text(json.dumps(value))
    before = path.read_bytes()
    project.stage(18931)
    assert path.read_bytes() == before
