"""Preserve installed distribution license texts and metadata with the payload."""
import argparse
import importlib.metadata
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tomllib


def is_license_text(name):
    return re.match(r'^(licen[cs]e|copying|copyright|notice)(?:[-_.]|$)', name, re.I) is not None


def supplemental_license(kind, name, version, output, assets=None):
    assets = assets or Path(__file__).with_name('license-assets')
    manifest = json.loads((assets / 'manifest.json').read_text(encoding='utf-8'))
    entry = next((row for row in manifest['licenses']
                  if (row['kind'], row['name'], row['version']) == (kind, name, version)), None)
    if entry is None:
        return [], None
    source = assets / entry['file']
    if source.is_symlink() or not source.resolve().is_relative_to(assets.resolve()):
        raise ValueError('Supplemental license must remain inside reviewed assets')
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != entry['sha256']:
        raise ValueError(f'Supplemental license hash mismatch: {name} {version}')
    folder = output / (f'rust/{name}-{version}' if kind == 'rust' else name)
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / entry['file']
    destination.write_bytes(data)
    return [str(destination.relative_to(output))], entry['source']


def required_cargo_packages(lock_path, target=None):
    if target is None:
        version = subprocess.check_output(['rustc', '-vV'], text=True, encoding='utf-8', timeout=30)
        target = next(line.split(': ', 1)[1] for line in version.splitlines() if line.startswith('host: '))
    tree = subprocess.check_output(['cargo', 'tree', '--locked', '--offline', '--target', target,
                                   '--manifest-path', str(lock_path.with_name('Cargo.toml')),
                                   '--edges', 'normal,build', '--prefix', 'none', '--no-dedupe'],
                                  text=True, encoding='utf-8', timeout=60)
    packages = {(match[1], match[2]) for line in tree.splitlines()
                if (match := re.match(r'([^ ]+) v([^ ]+)', line))}
    return target, packages


def preserve_source_archive(package, cargo_home, output, subdirectory='mpl-sources'):
    """Preserve a crates.io archive only after verifying its Cargo.lock checksum."""
    basename = f"{package['name']}-{package['version']}.crate"
    archives = sorted((cargo_home / 'registry/cache').glob(f'*/{basename}'))
    if not archives:
        raise ValueError(f'Rust source archive missing: {basename}')
    expected = package.get('checksum')
    if not expected or len(expected) != 64:
        raise ValueError(f'Missing Cargo.lock checksum for Rust source: {basename}')
    source = None
    for candidate in archives:
        if hashlib.sha256(candidate.read_bytes()).hexdigest() == expected:
            source = candidate
            break
    if source is None:
        raise ValueError(f'Rust source archive hash mismatch: {basename}')
    destination = output / subdirectory / basename
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return {'name': package['name'], 'version': package['version'], 'sha256': expected,
            'archive': str(destination.relative_to(output)),
            'source': f"https://static.crates.io/crates/{package['name']}/{basename}"}


def license_from_verified_archive(package, archive, output):
    """Read only LICENSE files and Cargo metadata from a checksum-verified .crate.

    Rust's cargo fetch downloads registry/cache archives without necessarily
    expanding them into registry/src. Never treat an unverified archive as source
    or extract arbitrary archive members to the destination.
    """
    basename = f"{package['name']}-{package['version']}"
    prefix = basename + '/'
    texts = []
    metadata = {}
    with tarfile.open(archive, mode='r:gz') as distribution:
        for member in distribution:
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            relative = PurePosixPath(member.name[len(prefix):])
            if (not relative.parts or relative.is_absolute()
                    or '..' in relative.parts or member.size > 2_000_000):
                continue
            if str(relative) == 'Cargo.toml':
                file = distribution.extractfile(member)
                if file is not None:
                    metadata = tomllib.loads(file.read().decode('utf-8'))['package']
            if not is_license_text(relative.name):
                continue
            file = distribution.extractfile(member)
            if file is None:
                continue
            destination = output / 'rust' / basename / Path(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(file.read())
            texts.append(str(destination.relative_to(output)))
    return texts, metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cargo-lock', type=Path)
    parser.add_argument('--cargo-home', type=Path)
    parser.add_argument('--cargo-target')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    entries = []
    for distribution in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name = distribution.metadata['Name']
        texts = []
        for item in distribution.files or []:
            if is_license_text(item.name):
                source = Path(distribution.locate_file(item))
                if source.is_file():
                    destination = args.output / name / Path(str(item)).name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
                    texts.append(str(destination.relative_to(args.output)))
        supplemental_source = None
        if not texts:
            texts, supplemental_source = supplemental_license('python', name, distribution.version, args.output)
        if not texts:
            raise ValueError(f'Python distribution has no preserved license text: {name} {distribution.version}')
        entries.append({'name': name, 'version': distribution.version,
                        'license': distribution.metadata.get('License-Expression') or distribution.metadata.get('License'),
                        'license_files': texts, 'home_page': distribution.metadata.get('Home-page'),
                        'supplemental_source': supplemental_source})
    (args.output / 'python-distributions.json').write_text(json.dumps(entries, indent=2), encoding='utf-8')
    if args.cargo_lock:
        packages = tomllib.loads(args.cargo_lock.read_text(encoding='utf-8'))['package']
        registries = list((args.cargo_home / 'registry/src').glob('*'))
        target, required = required_cargo_packages(args.cargo_lock, args.cargo_target)
        entries = []
        source_archives = []
        cached_archives = []
        for package in packages:
            if not package.get('source', '').startswith('registry+'):
                continue
            required_for_target = (package['name'], package['version']) in required
            directory = next((registry / f"{package['name']}-{package['version']}" for registry in registries
                              if (registry / f"{package['name']}-{package['version']}").is_dir()), None)
            metadata = tomllib.loads((directory / 'Cargo.toml').read_text(encoding='utf-8'))['package'] if directory else {}
            texts = []
            archive_record = None
            if directory:
                for source in directory.rglob('*'):
                    if source.is_file() and is_license_text(source.name):
                        destination = args.output / 'rust' / directory.name / source.relative_to(directory)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, destination)
                        texts.append(str(destination.relative_to(args.output)))
            elif required_for_target:
                # cargo fetch may have downloaded the verified .crate while
                # leaving registry/src empty; preserve the complete source
                # archive in the delivery instead of claiming source is absent.
                archive_record = preserve_source_archive(
                    package, args.cargo_home, args.output, subdirectory='rust-source-archives')
                archived_texts, metadata = license_from_verified_archive(
                    package, args.output / archive_record['archive'], args.output)
                texts.extend(archived_texts)
                cached_archives.append(archive_record)
            supplemental_source = None
            if not texts:
                texts, supplemental_source = supplemental_license('rust', package['name'], package['version'], args.output)
            source_available = directory is not None or archive_record is not None
            if required_for_target and (not source_available or not texts):
                raise ValueError(f'Target dependency has no preserved source/license: {package["name"]} {package["version"]}')
            if required_for_target and 'MPL' in (metadata.get('license') or ''):
                source_archives.append(preserve_source_archive(package, args.cargo_home, args.output))
            entries.append({'name': package['name'], 'version': package['version'], 'checksum': package.get('checksum'),
                            'license': metadata.get('license'), 'license_files': texts, 'source_available': source_available,
                            'source_archive': archive_record['archive'] if archive_record else None,
                            'required_for_target': required_for_target, 'target': target,
                            'supplemental_source': supplemental_source})
        (args.output / 'rust-resolved-dependencies.json').write_text(json.dumps(entries, indent=2), encoding='utf-8')
        (args.output / 'rust-source-archives.json').write_text(json.dumps(cached_archives, indent=2), encoding='utf-8')
        (args.output / 'mpl-source-archives.json').write_text(json.dumps(source_archives, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
