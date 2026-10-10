"""Per-user provider project, distinct from read-only installed resources.

This layer stages only reviewed public assets and persists user selection. It
does not infer readiness, execute manifests, or import developer local settings.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import re

from desktop_runtime.config import atomic_write
from provider.provider_manifest import load_provider_manifests, load_provider_manifest


PROVIDER_ID = re.compile(r'^[a-z0-9][a-z0-9_-]*$')


class ComponentProject:
    def __init__(self, data: Path, resources: Path):
        self.data = data.resolve()
        self.resources = resources.resolve()
        self.root = (self.data / 'components').resolve()
        self.manifest_dir = (self.root / 'provider_manifests').resolve()
        if not self.root.is_relative_to(self.data) or not self.manifest_dir.is_relative_to(self.root):
            raise ValueError('Component directories must remain inside private user data')
        self.preferences_path = self.data / 'config/provider-preferences.json'
        self.asset_record_path = self.root / 'builtin-manifests.json'
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)

    def preferences(self):
        if not self.preferences_path.exists():
            return {'enabled': []}
        value = json.loads(self.preferences_path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or set(value) != {'enabled'}:
            raise ValueError('Invalid provider preferences; existing file preserved')
        ids = value['enabled']
        if not isinstance(ids, list) or any(not isinstance(item, str) or not PROVIDER_ID.fullmatch(item) for item in ids):
            raise ValueError('Invalid enabled provider list')
        return {'enabled': sorted(set(ids))}

    def select(self, provider_id: str, enabled: bool):
        if not isinstance(provider_id, str) or not PROVIDER_ID.fullmatch(provider_id) or type(enabled) is not bool:
            raise ValueError('Invalid provider selection')
        if provider_id not in load_provider_manifests(self.root, self.manifest_dir):
            raise ValueError('Unknown provider manifest')
        selected = set(self.preferences()['enabled'])
        (selected.add if enabled else selected.discard)(provider_id)
        atomic_write(self.preferences_path, json.dumps({'enabled': sorted(selected)}, indent=2).encode())
        return self.preferences()

    def stage(self, browser_port: int):
        """Copy reviewed assets; never copy *.local.* or source user data."""
        templates = self.resources / 'provider-assets'
        asset_records = json.loads(self.asset_record_path.read_text()) if self.asset_record_path.exists() else {}
        if not isinstance(asset_records, dict) or any(not isinstance(key, str) or not PROVIDER_ID.fullmatch(key) or not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value) for key, value in asset_records.items()):
            raise ValueError('Invalid builtin manifest provenance record; existing files preserved')
        for folder, suffixes in (('providers', {'.py', '.mjs'}), ('provider_specs', {'.txt', '.json'})):
            source = templates / folder
            if source.is_dir():
                target = self.root / folder
                if not target.resolve().is_relative_to(self.root):
                    raise ValueError('Component asset directory escapes user data')
                target.mkdir(parents=True, exist_ok=True)
                for path in source.iterdir():
                    if path.is_file() and not path.is_symlink() and path.suffix in suffixes and '.local.' not in path.name:
                        atomic_write(target / path.name, path.read_bytes())
        config = templates / 'config/skill-library.json'
        if config.is_file() and not (self.root / 'config/skill-library.json').exists():
            atomic_write(self.root / 'config/skill-library.json', config.read_bytes())
        patches = templates / 'provider_patches'
        if patches.is_dir():
            for path in patches.rglob('*.patch'):
                destination = self.root / 'provider_patches' / path.relative_to(patches)
                if path.is_symlink() or not destination.resolve().is_relative_to(self.root):
                    raise ValueError('Invalid provider patch path')
                atomic_write(destination, path.read_bytes())
        source = templates / 'provider_manifests'
        if source.is_dir():
            for path in source.glob('*.json'):
                if path.is_symlink():
                    raise ValueError('Provider template must not be a symlink')
                value = json.loads(path.read_text(encoding='utf-8'))
                provider_id = value.get('id')
                if not isinstance(provider_id, str) or not PROVIDER_ID.fullmatch(provider_id):
                    raise ValueError('Invalid bundled provider ID')
                runtime = value['runtime']
                if runtime.get('command') == 'node.exe':
                    runtime['command'] = str(self.resources / 'node.exe')
                elif runtime.get('command') == 'WindowsPackageManagerMCPServer.exe':
                    # Missing optional system components must remain visible,
                    # rather than making the whole catalog fail to parse.
                    runtime['command'] = str(self.root / 'system-components/winget/WindowsPackageManagerMCPServer.exe')
                value['autostart'] = False
                if provider_id == 'browser':
                    runtime['url'] = f'http://localhost:{browser_port}/mcp'
                destination = self.manifest_dir / f'{provider_id}.json'
                generated = json.dumps(value, indent=2).encode()
                existing_digest = hashlib.sha256(destination.read_bytes()).hexdigest() if destination.exists() else None
                if existing_digest is None or existing_digest == asset_records.get(provider_id):
                    atomic_write(destination, generated)
                    asset_records[provider_id] = hashlib.sha256(generated).hexdigest()
                elif existing_digest == hashlib.sha256(generated).hexdigest():
                    asset_records[provider_id] = existing_digest
                elif provider_id == 'browser':
                    existing = json.loads(destination.read_text())
                    if existing['runtime'].get('url') != value['runtime']['url']:
                        existing['runtime']['url'] = value['runtime']['url']
                        atomic_write(destination, json.dumps(existing, indent=2).encode())
        # Compatibility with RC.3 payloads and isolated tests containing only
        # the standalone bundled browser template.
        browser = self.resources / 'browser-playwright.json'
        if browser.is_file():
            value = json.loads(browser.read_text(encoding='utf-8'))
            value['autostart'] = False
            value['runtime']['url'] = f'http://localhost:{browser_port}/mcp'
            destination = self.manifest_dir / 'browser.json'
            generated = json.dumps(value, indent=2).encode()
            previous = hashlib.sha256(destination.read_bytes()).hexdigest() if destination.exists() else None
            if previous is None or previous == asset_records.get('browser'):
                atomic_write(destination, generated)
                asset_records['browser'] = hashlib.sha256(generated).hexdigest()
        atomic_write(self.asset_record_path, json.dumps(asset_records, sort_keys=True).encode())
        return self.catalog()

    def configure_manifest(self, provider_id, content=None, *, confirm=False, expected_sha256=None, expected_content_sha256=None):
        if not isinstance(provider_id, str) or not PROVIDER_ID.fullmatch(provider_id) or type(confirm) is not bool:
            raise ValueError('Invalid provider configuration request')
        installed = load_provider_manifests(self.root, self.manifest_dir)
        path = installed[provider_id].path if provider_id in installed else self.manifest_dir / f'{provider_id}.json'
        if path.parent != self.manifest_dir:
            raise ValueError('Provider configuration must remain in its managed manifest directory')
        if not path.is_file() or path.is_symlink():
            raise ValueError('Provider manifest is missing or linked')
        current = path.read_bytes()
        current_digest = hashlib.sha256(current).hexdigest()
        if content is None:
            return {'provider_id': provider_id, 'content': current.decode('utf-8'), 'sha256': current_digest}
        if not isinstance(content, str) or len(content.encode()) > 50000:
            raise ValueError('Manifest must be bounded JSON text')
        if expected_sha256 != current_digest:
            raise ValueError('Provider configuration changed; reload its current version')
        proposal = json.loads(content)
        if not isinstance(proposal, dict) or proposal.get('id') != provider_id:
            raise ValueError('Editing cannot rename a provider; import a unique new ID')
        temporary = self.manifest_dir / f'{provider_id}.preview'
        try:
            atomic_write(temporary, content.encode())
            manifest = load_provider_manifest(temporary, self.root)
        finally:
            temporary.unlink(missing_ok=True)
        if manifest.provider_id != provider_id:
            raise ValueError('Editing cannot rename a provider; import a unique new ID')
        if provider_id == 'browser' and json.loads(current)['runtime'] != json.loads(content)['runtime']:
            raise ValueError('The bundled browser transport is managed; change its port in settings or import a separate browser ID')
        if not manifest.tool_allowlist:
            raise ValueError('Managed providers require an explicit nonempty tool_allowlist')
        proposed_digest = hashlib.sha256(content.encode()).hexdigest()
        result = {**manifest.summary(), 'manifest': str(path), 'args': list(manifest.args),
                  'tool_overrides': manifest.tool_overrides, 'current_sha256': current_digest,
                  'content_sha256': proposed_digest, 'saved': False, 'reloaded': False}
        if confirm:
            if expected_content_sha256 != proposed_digest:
                raise ValueError('Review the exact proposed manifest before saving')
            atomic_write(path, content.encode())
            result['saved'] = True
        return result

    def import_manifest(self, content: str, *, confirm: bool = False, expected_sha256: str | None = None):
        if not isinstance(content, str) or len(content.encode()) > 50000:
            raise ValueError('Manifest must be bounded JSON text')
        value = json.loads(content)
        if not isinstance(value, dict) or not isinstance(value.get('id'), str) or not PROVIDER_ID.fullmatch(value['id']):
            raise ValueError('Invalid provider ID')
        provider_id = value['id']
        destination = self.manifest_dir / f'{provider_id}.json'
        if destination.exists() or provider_id in load_provider_manifests(self.root, self.manifest_dir):
            raise ValueError('Provider already exists; import requires a new unique ID')
        # Only the validated original manifest schema is supported. Importing is
        # never launching: activation remains a separate confirmed broker call.
        temporary = self.manifest_dir / f'{provider_id}.preview'
        try:
            atomic_write(temporary, content.encode())
            manifest = load_provider_manifest(temporary, self.root)
        finally:
            temporary.unlink(missing_ok=True)
        if not manifest.tool_allowlist:
            raise ValueError('Imported providers require an explicit nonempty tool_allowlist')
        digest = hashlib.sha256(content.encode()).hexdigest()
        preview = {**manifest.summary(), 'manifest': str(destination), 'sha256': digest,
                   'args': list(manifest.args), 'tool_overrides': manifest.tool_overrides,
                   'imported': False, 'launched': False}
        if confirm:
            if expected_sha256 != digest:
                raise ValueError('Review the exact manifest SHA-256 before import')
            atomic_write(destination, content.encode())
            preview['imported'] = True
        return preview

    def catalog(self):
        selected = set(self.preferences()['enabled'])
        return {'project_directory': str(self.root), 'manifest_directory': str(self.manifest_dir),
                'providers': [{**manifest.summary(), 'requested_enabled': provider_id in selected}
                              for provider_id, manifest in sorted(load_provider_manifests(self.root, self.manifest_dir).items())]}
