"""Reviewed extraction of the installed public source snapshot into an empty root."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import zipfile


def prepare_source(data, resources, path, confirmed, expected_sha256):
    if type(confirmed) is not bool or not isinstance(path, str):
        raise ValueError('Invalid development source preparation request')
    target = Path(path)
    if not target.is_absolute() or path.startswith(('\\\\', '//')):
        raise ValueError('Select an absolute local empty directory')
    target = target.resolve()
    for protected in (data.resolve(), resources.resolve()):
        if target == protected or target in protected.parents or protected in target.parents:
            raise ValueError('Development source must not overlap private data or installed resources')
    if not target.is_dir() or any(target.iterdir()):
        raise ValueError('Select an existing empty directory; existing files will not be replaced')
    snapshot = resources / 'developer-source.zip'
    if not snapshot.is_file():
        raise ValueError('This installation lacks its source snapshot; select an existing complete source workspace')
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    with zipfile.ZipFile(snapshot) as archive:
        manifest = json.loads(archive.read('PLA-SOURCE-MANIFEST.json'))
        rows = manifest['files']
        if len(rows) > 10000 or sum(row['bytes'] for row in rows) > 100_000_000:
            raise ValueError('Unexpectedly large source snapshot')
        names = set()
        for row in rows:
            raw = row['path']
            relative = PurePosixPath(raw)
            if not isinstance(raw, str) or not relative.parts or relative.is_absolute() or '..' in relative.parts or '\\' in raw or ':' in raw:
                raise ValueError('Invalid source snapshot path')
            if raw.casefold() in names or not (target / raw).resolve().is_relative_to(target):
                raise ValueError('Duplicate or escaping source snapshot entry')
            names.add(raw.casefold())
            payload = archive.read(raw)
            if len(payload) != row['bytes'] or hashlib.sha256(payload).hexdigest() != row['sha256']:
                raise ValueError('Source snapshot file integrity check failed')
        plan_digest = hashlib.sha256(json.dumps({'target': str(target), 'snapshot_sha256': digest}, sort_keys=True).encode()).hexdigest()
        git = shutil.which('git.exe') or shutil.which('git')
        preview = {'target': str(target), 'sha256': plan_digest, 'snapshot_sha256': digest, 'source_commit': manifest['source_commit'],
                   'file_count': len(rows), 'bytes': sum(row['bytes'] for row in rows), 'prepared': False, 'development_enabled': False}
        if not confirmed:
            return preview
        if expected_sha256 != plan_digest:
            raise ValueError('Review the exact source snapshot SHA-256 before creating the workspace')
        with tempfile.TemporaryDirectory(prefix='.pla-source-', dir=target) as temporary:
            staging = Path(temporary)
            for row in rows:
                destination = staging / row['path']
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(row['path']))
            (staging / 'PLA-SOURCE-MANIFEST.json').write_bytes(archive.read('PLA-SOURCE-MANIFEST.json'))
            if any(item != staging for item in target.iterdir()):
                raise ValueError('Destination changed during source preparation; existing files preserved')
            for item in staging.iterdir():
                item.rename(target / item.name)
        preview['prepared'] = True
        preview['git_initialized'] = False
        if git:
            env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
            env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')
            def run_git(*arguments):
                subprocess.run([git, '-c', 'core.autocrlf=false', '-c', 'commit.gpgsign=false', *arguments],
                    cwd=target, env=env, check=True, capture_output=True, timeout=30,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            run_git('init', '--template=', '--initial-branch=main', '-q')
            hooks = target / '.git/pla-empty-hooks'
            hooks.mkdir()
            run_git('-c', f'core.hooksPath={hooks}', 'add', '--all')
            run_git('-c', f'core.hooksPath={hooks}', '-c', 'user.name=PLA Desktop', '-c', 'user.email=local@pla.invalid',
                    'commit', '-q', '-m', 'Initialize reviewed PLA Desktop source snapshot')
            preview['git_initialized'] = True
        return preview
