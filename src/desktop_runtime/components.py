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
from provider.provider_repository import RepositoryClient, load_repository_config, save_repository_config
from provider.provider_lifecycle import ProviderLifecycleManager


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
        self.repositories_config = self.data / 'config/provider-repositories.json'
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)

        # State inspection is available before the optional uv bootstrap.
        self._lifecycle_manager = ProviderLifecycleManager(
            component_root=self.root, manifest_dir=self.manifest_dir,
            uv_exe=self.root / 'tools/uv.exe', python_version='3.11.9', resources=self.resources)

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
        # Retire only the unchanged, provenance-tracked builtin DOCX manifest.
        # User imports/edits and installed environments remain user-owned.
        retired = self.manifest_dir / 'docx.json'
        if (retired.is_file() and not retired.is_symlink()
                and hashlib.sha256(retired.read_bytes()).hexdigest() == asset_records.get('docx')
                and not (templates / 'provider_manifests/docx.json').exists()):
            preferences = self.preferences()
            preferences['enabled'] = [name for name in preferences['enabled'] if name != 'docx']
            atomic_write(self.preferences_path, json.dumps(preferences).encode())
            retired.unlink()
            asset_records.pop('docx', None)
            spec = self.root / 'provider_specs/docx.txt'
            if (spec.is_file() and not spec.is_symlink()
                    and spec.read_bytes().replace(b'\r\n', b'\n') == b'docx-mcp-server==0.7.4\nmcp==1.30.0\n'):
                spec.unlink()
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
                    from desktop_runtime.system_components import official_winget_mcp
                    official = official_winget_mcp()
                    # Microsoft's App Installer supplies this MCP. Never download
                    # an unsigned copy or assume winget.exe itself is the MCP.
                    # Retain the placeholder when system prerequisites are absent.
                    runtime['command'] = str(official or (
                        self.root / 'system-components/winget/WindowsPackageManagerMCPServer.exe'))
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

    # Repository and remote provider management

    def list_repositories(self):
        """List configured provider repositories."""
        if not self.repositories_config.exists():
            return {'repositories': []}

        data = json.loads(self.repositories_config.read_text(encoding='utf-8'))
        return {
            'repositories': data.get('repositories', [])
        }

    def add_repository(self, url: str, name: str = '', enabled: bool = True) -> dict:
        """Add a provider repository.

        Args:
            url: Repository URL (file://, https://)
            name: Optional repository name
            enabled: Whether repository is enabled

        Returns:
            Updated repository configuration
        """
        if not url or not isinstance(url, str):
            raise ValueError('Invalid repository URL')

        # Load existing
        repos = self.list_repositories()['repositories']

        # Check for duplicates
        if any(r['url'] == url for r in repos):
            raise ValueError('Repository already exists')

        # Validate by trying to fetch index
        try:
            client = RepositoryClient(url, self.root / 'repository-cache')
            info = client.get_info()
        except Exception as exc:
            raise ValueError(f'Failed to access repository: {exc}')

        # Add repository
        repos.append({
            'url': url,
            'name': name or info.name,
            'repository_id': info.repository_id,
            'enabled': enabled,
            'added_at': json.loads(json.dumps(__import__('datetime').datetime.utcnow().isoformat() + 'Z'))
        })

        save_repository_config(self.repositories_config, repos)
        return self.list_repositories()

    def remove_repository(self, url: str) -> dict:
        """Remove a provider repository."""
        repos = self.list_repositories()['repositories']
        repos = [r for r in repos if r['url'] != url]
        save_repository_config(self.repositories_config, repos)
        return self.list_repositories()

    def discover_providers(self, repository_url: str | None = None):
        """Discover available providers from repositories.

        Args:
            repository_url: Optional specific repository, or None for all enabled

        Returns:
            List of available providers with installation status
        """
        if repository_url:
            clients = [RepositoryClient(repository_url, self.root / 'repository-cache')]
        else:
            clients = load_repository_config(self.repositories_config)

        if not clients:
            return {'providers': [], 'message': 'No repositories configured'}

        # Get installed providers
        installed = {}
        if self._lifecycle_manager:
            for record in self._lifecycle_manager.list_installed_providers():
                installed[record.provider_id] = record

        # Discover from all repositories
        discovered = []
        for client in clients:
            try:
                repo_info = client.get_info()
                providers = client.list_providers()

                for provider in providers:
                    installed_record = installed.get(provider.provider_id)
                    discovered.append({
                        'provider_id': provider.provider_id,
                        'name': provider.name,
                        'description': provider.description,
                        'category': provider.category,
                        'latest_version': provider.latest_version,
                        'repository_id': provider.repository_id,
                        'repository_url': repo_info.url,
                        'installed': installed_record is not None,
                        'installed_version': installed_record.version if installed_record else None,
                        'installed_state': installed_record.state if installed_record else None,
                        'upgrade_available': (
                            installed_record is not None and
                            installed_record.version != provider.latest_version
                        )
                    })
            except Exception as exc:
                # Skip failed repositories
                continue

        return {
            'providers': sorted(discovered, key=lambda p: p['provider_id']),
            'total': len(discovered),
            'installed_count': sum(1 for p in discovered if p['installed'])
        }

    def get_provider_details(self, provider_id: str, repository_url: str):
        """Get detailed information about a provider.

        Args:
            provider_id: Provider ID
            repository_url: Repository URL

        Returns:
            Provider metadata and available versions
        """
        client = RepositoryClient(repository_url, self.root / 'repository-cache')

        try:
            metadata = client.get_provider_metadata(provider_id)
            versions = client.list_versions(provider_id)

            # Check installation status
            installed_record = None
            if self._lifecycle_manager:
                installed_record = self._lifecycle_manager.get_provider_state(provider_id)

            return {
                'provider_id': metadata.provider_id,
                'name': metadata.name,
                'description': metadata.description,
                'author': metadata.author,
                'license': metadata.license,
                'homepage': metadata.homepage,
                'category': metadata.category,
                'tags': metadata.tags,
                'minimum_pla_version': metadata.minimum_pla_version,
                'versions': [
                    {
                        'version': v.version,
                        'release_date': v.release_date,
                        'stability': v.stability,
                        'breaking_changes': v.breaking_changes,
                        'changelog': v.changelog,
                        'minimum_pla_version': v.minimum_pla_version,
                        'deprecated': v.deprecated
                    }
                    for v in versions
                ],
                'installed': installed_record is not None,
                'installed_version': installed_record.version if installed_record else None,
                'installed_state': installed_record.state if installed_record else None
            }
        except Exception as exc:
            raise ValueError(f'Failed to get provider details: {exc}')

    def install_provider_from_repository(
        self,
        provider_id: str,
        repository_url: str,
        version: str | None = None,
        progress_callback=None
    ):
        """Install a provider from a repository.

        Args:
            provider_id: Provider to install
            repository_url: Source repository URL
            version: Specific version, or None for latest
            progress_callback: Optional callback(message, percentage)

        Returns:
            Installation result
        """
        if not self._lifecycle_manager:
            raise ValueError('Lifecycle manager not initialized (uv.exe not found)')

        # Check if already installed
        installed = self._lifecycle_manager.get_provider_state(provider_id)
        if installed and installed.state not in {'not_installed', 'failed'}:
            raise ValueError(f'Provider {provider_id} is already installed')

        # Get provider info
        client = RepositoryClient(repository_url, self.root / 'repository-cache')

        if version is None:
            # Get latest version
            versions = client.list_versions(provider_id)
            if not versions:
                raise ValueError(f'No versions available for {provider_id}')
            version = versions[0].version

        # Download package
        if progress_callback:
            progress_callback(f'Downloading {provider_id} v{version}', 5)

        package = client.download_package(provider_id, version)

        # Install
        try:
            record = self._lifecycle_manager.install_provider(
                package=package,
                repository_url=repository_url,
                progress_callback=progress_callback
            )

            return {
                'provider_id': provider_id,
                'version': version,
                'state': record.state,
                'installed_at': record.installed_at,
                'success': True
            }
        except Exception as exc:
            return {
                'provider_id': provider_id,
                'version': version,
                'success': False,
                'error': str(exc)
            }

    def upgrade_provider(
        self,
        provider_id: str,
        repository_url: str,
        to_version: str | None = None,
        progress_callback=None
    ):
        """Upgrade an installed provider.

        Args:
            provider_id: Provider to upgrade
            repository_url: Source repository URL
            to_version: Target version, or None for latest
            progress_callback: Optional callback(message, percentage)

        Returns:
            Upgrade result
        """
        if not self._lifecycle_manager:
            raise ValueError('Lifecycle manager not initialized')

        # Check current installation
        current = self._lifecycle_manager.get_provider_state(provider_id)
        if not current or current.state == 'not_installed':
            raise ValueError(f'Provider {provider_id} is not installed')

        # Get target version
        client = RepositoryClient(repository_url, self.root / 'repository-cache')

        if to_version is None:
            versions = client.list_versions(provider_id)
            if not versions:
                raise ValueError(f'No versions available for {provider_id}')
            to_version = versions[0].version

        if to_version == current.version:
            return {
                'provider_id': provider_id,
                'current_version': current.version,
                'target_version': to_version,
                'success': True,
                'message': 'Already at target version'
            }

        # Download new package
        if progress_callback:
            progress_callback(f'Downloading {provider_id} v{to_version}', 5)

        package = client.download_package(provider_id, to_version)

        # Upgrade
        try:
            record = self._lifecycle_manager.upgrade_provider(
                provider_id=provider_id,
                new_package=package,
                repository_url=repository_url,
                progress_callback=progress_callback
            )

            return {
                'provider_id': provider_id,
                'previous_version': current.version,
                'new_version': record.version,
                'state': record.state,
                'success': True
            }
        except Exception as exc:
            return {
                'provider_id': provider_id,
                'success': False,
                'error': str(exc)
            }

    def uninstall_provider(self, provider_id: str, progress_callback=None):
        """Uninstall a provider completely.

        Args:
            provider_id: Provider to uninstall
            progress_callback: Optional callback(message, percentage)

        Returns:
            Uninstallation result
        """
        if not self._lifecycle_manager:
            raise ValueError('Lifecycle manager not initialized')

        try:
            self._lifecycle_manager.uninstall_provider(
                provider_id=provider_id,
                progress_callback=progress_callback
            )

            return {
                'provider_id': provider_id,
                'success': True,
                'message': 'Successfully uninstalled'
            }
        except Exception as exc:
            return {
                'provider_id': provider_id,
                'success': False,
                'error': str(exc)
            }

    def check_provider_health(self, provider_id: str):
        """Check health of installed provider.

        Args:
            provider_id: Provider to check

        Returns:
            Health check result
        """
        if not self._lifecycle_manager:
            return {
                'provider_id': provider_id,
                'status': 'unavailable',
                'message': 'Lifecycle manager not initialized'
            }

        return self._lifecycle_manager.health_check(provider_id)

    def list_installed_providers(self):
        """List all installed providers with their states."""
        if not self._lifecycle_manager:
            return {'providers': []}

        records = self._lifecycle_manager.list_installed_providers()

        return {
            'providers': [
                {
                    'provider_id': r.provider_id,
                    'version': r.version,
                    'state': r.state,
                    'installed_at': r.installed_at,
                    'repository_url': r.repository_url,
                    'health_check_last': r.health_check_last,
                    'health_check_status': r.health_check_status,
                    'failure_reason': r.failure_reason
                }
                for r in records
            ],
            'total': len(records)
        }
