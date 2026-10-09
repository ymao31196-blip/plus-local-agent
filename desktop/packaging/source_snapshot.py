"""Deterministic tracked public source snapshot for opt-in Desktop self-development."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root).decode().strip()
    entries = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(filter(None, tracked)):
            relative = Path(name)
            if name == 'PLA-SOURCE-MANIFEST.json' or relative.parts[0] in {'workspace', 'state', 'cache', 'dist', '.desktop-build', '.provider_envs', '.provider_sources'} or '.local.' in relative.name or relative.name.startswith('.env'):
                continue
            path = root / relative
            if not path.is_file() or path.is_symlink():
                raise ValueError(f'Invalid tracked snapshot file: {name}')
            data = path.read_bytes()
            entry = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data)
            entries.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        entry = zipfile.ZipInfo('PLA-SOURCE-MANIFEST.json', (2026, 1, 1, 0, 0, 0))
        entry.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(entry, json.dumps({'source_commit': revision, 'files': entries}, indent=2).encode())


if __name__ == '__main__':
    main()
