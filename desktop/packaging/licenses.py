"""Preserve installed distribution license texts and metadata with the payload."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import tomllib


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cargo-lock', type=Path)
    parser.add_argument('--cargo-home', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    entries = []
    for distribution in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name = distribution.metadata['Name']
        texts = []
        for item in distribution.files or []:
            if any(token in item.name.lower() for token in ('license', 'copying', 'notice')) and '.dist-info' in str(item):
                source = Path(distribution.locate_file(item))
                if source.is_file():
                    destination = args.output / name / Path(str(item)).name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
                    texts.append(str(destination.relative_to(args.output)))
        entries.append({'name': name, 'version': distribution.version,
                        'license': distribution.metadata.get('License-Expression') or distribution.metadata.get('License'),
                        'license_files': texts, 'home_page': distribution.metadata.get('Home-page')})
    (args.output / 'python-distributions.json').write_text(json.dumps(entries, indent=2), encoding='utf-8')
    if args.cargo_lock:
        packages = tomllib.loads(args.cargo_lock.read_text(encoding='utf-8'))['package']
        registries = list((args.cargo_home / 'registry/src').glob('*'))
        entries = []
        for package in packages:
            if not package.get('source', '').startswith('registry+'):
                continue
            directory = next((registry / f"{package['name']}-{package['version']}" for registry in registries
                              if (registry / f"{package['name']}-{package['version']}").is_dir()), None)
            metadata = tomllib.loads((directory / 'Cargo.toml').read_text(encoding='utf-8'))['package'] if directory else {}
            texts = []
            if directory:
                for source in directory.iterdir():
                    if source.is_file() and source.name.lower().startswith(('license', 'copying', 'notice')):
                        destination = args.output / 'rust' / directory.name / source.name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, destination)
                        texts.append(str(destination.relative_to(args.output)))
            entries.append({'name': package['name'], 'version': package['version'], 'checksum': package.get('checksum'),
                            'license': metadata.get('license'), 'license_files': texts, 'source_available': directory is not None})
        (args.output / 'rust-resolved-dependencies.json').write_text(json.dumps(entries, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
