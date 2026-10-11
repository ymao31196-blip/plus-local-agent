"""Provider Repository client for discovering and downloading MCP providers.

Supports local file and HTTPS repository sources with checksum verification.
Git repository support is not implemented.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import os
import time
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

SCHEMA_VERSION = 1
PROVIDER_VERSION_RE = re.compile(r'^\d+\.\d+\.\d+$')


def checked_identity(provider_id, version=None):
    if not isinstance(provider_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', provider_id):
        raise ValueError('Invalid provider ID')
    if version is not None and (not isinstance(version, str) or not PROVIDER_VERSION_RE.fullmatch(version)):
        raise ValueError('Invalid provider version')


def checked_relative_path(value):
    if not isinstance(value, str) or not value or any(c in value for c in ('\\', ':', '\0', '?', '#')):
        raise ValueError('Invalid repository asset path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {'', '.', '..'} for part in value.split('/')):
        raise ValueError('Repository asset path escapes its package')
    if any(part.rstrip(' .') != part for part in path.parts):
        raise ValueError('Invalid Windows asset path')
    reserved = {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
    if any(part.split('.')[0].upper() in reserved or any(ord(c)<32 or c in '<>"|*' for c in part) for part in path.parts):
        raise ValueError('Reserved Windows asset path')
    return path


@dataclass(frozen=True)
class RepositoryInfo:
    """Repository metadata."""
    repository_id: str
    name: str
    description: str
    url: str
    schema_version: int

    @staticmethod
    def from_dict(data: dict[str, Any], url: str) -> RepositoryInfo:
        if data.get('schema_version') != SCHEMA_VERSION:
            raise ValueError(f"Unsupported repository schema version: {data.get('schema_version')}")
        return RepositoryInfo(
            repository_id=data['repository_id'],
            name=data['name'],
            description=data['description'],
            url=url,
            schema_version=data['schema_version']
        )


@dataclass(frozen=True)
class ProviderSummary:
    """Brief provider information from repository index."""
    provider_id: str
    name: str
    description: str
    category: str
    latest_version: str
    repository_id: str
    repository_path: str

    @staticmethod
    def from_dict(data: dict[str, Any], repository_id: str) -> ProviderSummary:
        provider_id = data.get('provider_id', '')
        if not isinstance(provider_id, str) or not re.fullmatch(r'^[a-z0-9][a-z0-9_-]*$', provider_id):
            raise ValueError(f"Invalid provider_id: {provider_id!r}")

        latest = data.get('latest_version', '')
        if not PROVIDER_VERSION_RE.fullmatch(latest):
            raise ValueError(f"Invalid version format: {latest!r}")

        return ProviderSummary(
            provider_id=provider_id,
            name=data.get('name', ''),
            description=data.get('description', ''),
            category=data.get('category', 'general'),
            latest_version=latest,
            repository_id=repository_id,
            repository_path=data.get('repository_path', '')
        )


@dataclass(frozen=True)
class ProviderMetadata:
    """Detailed provider information."""
    provider_id: str
    name: str
    description: str
    author: str
    license: str
    homepage: str
    category: str
    tags: list[str]
    minimum_pla_version: str
    repository_path: str

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ProviderMetadata:
        if data.get('schema_version') != SCHEMA_VERSION:
            raise ValueError(f"Unsupported metadata schema version: {data.get('schema_version')}")

        return ProviderMetadata(
            provider_id=data['provider_id'],
            name=data['name'],
            description=data['description'],
            author=data.get('author', 'Unknown'),
            license=data.get('license', 'Unknown'),
            homepage=data.get('homepage', ''),
            category=data.get('category', 'general'),
            tags=data.get('tags', []),
            minimum_pla_version=data.get('minimum_pla_version', '2.0.0'),
            repository_path=data['repository_path']
        )


@dataclass(frozen=True)
class ProviderVersion:
    """Provider version information."""
    version: str
    release_date: str
    stability: Literal['stable', 'beta', 'alpha']
    breaking_changes: bool
    changelog: str
    minimum_pla_version: str
    deprecated: bool

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ProviderVersion:
        version = data.get('version', '')
        if not PROVIDER_VERSION_RE.fullmatch(version):
            raise ValueError(f"Invalid version format: {version!r}")

        stability = data.get('stability', 'stable')
        if stability not in {'stable', 'beta', 'alpha'}:
            raise ValueError(f"Invalid stability: {stability!r}")

        return ProviderVersion(
            version=version,
            release_date=data.get('release_date', ''),
            stability=stability,
            breaking_changes=bool(data.get('breaking_changes', False)),
            changelog=data.get('changelog', ''),
            minimum_pla_version=data.get('minimum_pla_version', '2.0.0'),
            deprecated=bool(data.get('deprecated', False))
        )


@dataclass(frozen=True)
class ProviderPackage:
    """Complete provider package for installation."""
    provider_id: str
    version: str
    spec: dict[str, Any]
    manifest: dict[str, Any]
    checksums: dict[str, str]
    assets: dict[str, bytes]  # relative path -> content
    metadata_bytes: dict[str, bytes] = field(default_factory=dict)

    def verify_checksums(self) -> None:
        """Verify all asset checksums match the checksums.json."""
        checked_identity(self.provider_id, self.version)
        if self.spec.get('provider_id') != self.provider_id or self.spec.get('version') != self.version or self.manifest.get('id') != self.provider_id:
            raise ValueError('Package identity does not match its request')
        if self.spec.get('schema_version') != 1 or self.manifest.get('schema_version') != 1 or self.checksums.get('schema_version') != 1:
            raise ValueError('Unsupported package schema')
        files = self.checksums.get('files', {})
        if not isinstance(files, dict) or len(files)>100 or len({name.casefold() for name in files}) != len(files):
            raise ValueError('Invalid or ambiguous package file inventory')
        if set(files) != {'spec.json', 'manifest.json', *self.assets}:
            raise ValueError('Package checksum inventory must cover every file exactly')
        metadata = {'spec.json': json.dumps(self.spec, indent=2, sort_keys=True).encode('utf-8'),
                    'manifest.json': json.dumps(self.manifest, indent=2, sort_keys=True).encode('utf-8')}
        for name, value in self.metadata_bytes.items():
            if name not in metadata or json.loads(value.decode('utf-8')) != (self.spec if name == 'spec.json' else self.manifest):
                raise ValueError('Package metadata bytes do not match parsed content')
            metadata[name] = value
        for path, content in {**metadata, **self.assets}.items():
            checked_relative_path(path)
            expected = self.checksums.get('files', {}).get(path)
            if expected is None:
                raise ValueError(f"Missing checksum for asset: {path}")

            actual = f"sha256:{hashlib.sha256(content).hexdigest()}"
            if actual != expected:
                raise ValueError(
                    f"Checksum mismatch for {path}: expected {expected}, got {actual}"
                )


class RepositoryClient:
    """Client for accessing provider repositories."""

    def __init__(self, repository_url: str, cache_dir: Path | None = None):
        """Initialize repository client.

        Args:
            repository_url: Repository URL (file://, https://, git+https://)
            cache_dir: Local cache directory for downloaded files
        """
        self.repository_url = repository_url.rstrip('/')
        self.cache_dir = cache_dir or Path(tempfile.gettempdir()) / 'pla-repo-cache'
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Parse repository type
        if repository_url.startswith('file://'):
            self.repo_type = 'file'
            parsed = urllib.parse.urlsplit(repository_url)
            if parsed.netloc not in {'','localhost'} or parsed.query or parsed.fragment:
                raise ValueError('Local repository requires a local file URL')
            file_path = urllib.request.url2pathname(parsed.path)
            if os.name == 'nt' and file_path.startswith('\\') and re.match(r'^\\[A-Za-z]:',file_path):
                file_path = file_path[1:]
            self.repo_root = Path(file_path).resolve()
        elif repository_url.startswith(('http://', 'https://')):
            self.repo_type = 'http'
            if not repository_url.startswith('https://'):
                raise ValueError('Remote repositories require HTTPS')
            parsed = urllib.parse.urlsplit(repository_url)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Repository URL must not include credentials, query or fragment')
        elif repository_url.startswith('git+'):
            self.repo_type = 'git'
            raise NotImplementedError("Git repositories not yet implemented")
        else:
            raise ValueError(f"Unsupported repository URL: {repository_url}")

    def _fetch_json(self, path: str) -> dict[str, Any]:
        """Fetch and parse JSON file from repository."""
        return json.loads(self._fetch_bytes(path).decode('utf-8'))

    def _fetch_bytes(self, path: str) -> bytes:
        """Fetch raw bytes from repository."""
        checked_relative_path(path)
        remaining = 30 if not hasattr(self, '_download_deadline') else min(30, self._download_deadline-time.monotonic())
        if remaining <= 0:
            raise ValueError('Repository download exceeded its time limit')
        if self.repo_type == 'file':
            file_path = self.repo_root / path.lstrip('/')
            if not file_path.resolve().is_relative_to(self.repo_root):
                raise ValueError('Repository file escapes repository root')
            if not file_path.is_file():
                raise FileNotFoundError(f"Repository file not found: {path}")
            if file_path.stat().st_size > 10_000_000:
                raise ValueError('Repository file exceeds size limit')
            return file_path.read_bytes()

        elif self.repo_type == 'http':
            url = f"{self.repository_url}/{urllib.parse.quote(path, safe='/')}"
            with urllib.request.urlopen(url, timeout=remaining) as response:
                if not response.geturl().startswith('https://'):
                    raise ValueError('Repository redirects must retain HTTPS')
                content = response.read(10_000_001)
                if len(content) > 10_000_000:
                    raise ValueError('Repository file exceeds size limit')
                return content

        raise NotImplementedError(f"Unsupported repo type: {self.repo_type}")

    def get_info(self) -> RepositoryInfo:
        """Get repository metadata."""
        data = self._fetch_json('index.json')
        return RepositoryInfo.from_dict(data, self.repository_url)

    def list_providers(self) -> list[ProviderSummary]:
        """List all available providers in repository."""
        data = self._fetch_json('index.json')
        info = RepositoryInfo.from_dict(data, self.repository_url)

        providers = []
        for item in data.get('providers', []):
            try:
                providers.append(ProviderSummary.from_dict(item, info.repository_id))
            except (KeyError, ValueError) as exc:
                # Skip malformed entries
                continue

        return sorted(providers, key=lambda p: p.provider_id)

    def get_provider_metadata(self, provider_id: str) -> ProviderMetadata:
        """Get detailed provider metadata."""
        checked_identity(provider_id)
        path = f"providers/{provider_id}/metadata.json"
        data = self._fetch_json(path)
        return ProviderMetadata.from_dict(data)

    def list_versions(self, provider_id: str) -> list[ProviderVersion]:
        """List available versions for a provider."""
        checked_identity(provider_id)
        path = f"providers/{provider_id}/versions/index.json"
        data = self._fetch_json(path)

        if data.get('schema_version') != SCHEMA_VERSION:
            raise ValueError(f"Unsupported versions schema: {data.get('schema_version')}")

        versions = []
        for item in data.get('versions', []):
            try:
                versions.append(ProviderVersion.from_dict(item))
            except (KeyError, ValueError):
                continue

        # Sort by version (newest first)
        return sorted(
            versions,
            key=lambda v: tuple(int(x) for x in v.version.split('.')),
            reverse=True
        )

    def download_package(
        self,
        provider_id: str,
        version: str
    ) -> ProviderPackage:
        """Download and verify a complete provider package."""
        checked_identity(provider_id, version)
        self._download_deadline = time.monotonic()+60
        base_path = f"providers/{provider_id}/versions/{version}"

        # Fetch metadata files
        metadata_bytes = {name: self._fetch_bytes(f'{base_path}/{name}') for name in ('spec.json', 'manifest.json')}
        spec = json.loads(metadata_bytes['spec.json'].decode('utf-8'))
        manifest = json.loads(metadata_bytes['manifest.json'].decode('utf-8'))
        checksums = self._fetch_json(f"{base_path}/checksums.json")

        # Verify spec and manifest against checksums
        spec_bytes = json.dumps(spec, indent=2, sort_keys=True).encode('utf-8')
        spec_hash = f"sha256:{hashlib.sha256(spec_bytes).hexdigest()}"

        manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode('utf-8')
        manifest_hash = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"

        # Note: checksums.json itself is trusted from the repository
        # In production, this would be verified against a repository signature

        # Download all assets
        assets: dict[str, bytes] = {}
        if len(checksums.get('files', {})) > 100:
            raise ValueError('Provider package has too many assets')
        for asset_path in checksums.get('files', {}).keys():
            if asset_path in {'manifest.json', 'spec.json'}:
                continue

            full_path = f"{base_path}/{asset_path}"
            try:
                content = self._fetch_bytes(full_path)
                assets[asset_path] = content
                if sum(map(len, assets.values())) > 25_000_000:
                    raise ValueError('Provider package exceeds size limit')
            except Exception as exc:
                raise ValueError(f"Failed to download asset {asset_path}: {exc}")

        package = ProviderPackage(
            provider_id=provider_id,
            version=version,
            spec=spec,
            manifest=manifest,
            checksums=checksums,
            assets=assets,
            metadata_bytes=metadata_bytes,
        )

        # Verify all checksums
        package.verify_checksums()

        return package


def load_repository_config(config_path: Path) -> list[RepositoryClient]:
    """Load repository configuration and create clients."""
    if not config_path.exists():
        return []

    data = json.loads(config_path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or 'repositories' not in data:
        raise ValueError("Invalid repository configuration")

    clients = []
    for repo in data['repositories']:
        if not repo.get('enabled', True):
            continue

        url = repo.get('url')
        if not url:
            continue

        cache_dir = Path(repo['cache_dir']) if 'cache_dir' in repo else None
        clients.append(RepositoryClient(url, cache_dir))

    return clients


def save_repository_config(config_path: Path, repositories: list[dict[str, Any]]) -> None:
    """Save repository configuration."""
    config_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        'schema_version': 1,
        'repositories': repositories
    }

    content = json.dumps(data, indent=2).encode('utf-8')
    from desktop_runtime.config import atomic_write
    atomic_write(config_path, content)
