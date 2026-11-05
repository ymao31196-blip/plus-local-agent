"""Rust license compliance when cargo has fetched but not unpacked a crate."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pla_license_archive_packager", ROOT / "desktop/packaging/licenses.py")
packager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packager)


def _archive_fixture(root: Path):
    package = {"name": "example-rust-crate", "version": "1.2.3"}
    prefix = "example-rust-crate-1.2.3"
    cache = root / "cargo" / "registry" / "cache" / "registry-test"
    cache.mkdir(parents=True)
    archive = cache / f"{prefix}.crate"
    with tarfile.open(archive, mode="w:gz") as stream:
        for name, content in (
            (f"{prefix}/Cargo.toml", b'[package]\nname="example-rust-crate"\nversion="1.2.3"\nlicense="MIT"\n'),
            (f"{prefix}/LICENSE", b"Fixture license text\n"),
        ):
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            stream.addfile(entry, io.BytesIO(content))
    package["checksum"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    return package, archive


def test_checked_crate_archive_preserves_source_and_license():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        package, archive = _archive_fixture(root)
        output = root / "output"
        record = packager.preserve_source_archive(
            package, root / "cargo", output, subdirectory="rust-source-archives")
        assert (output / record["archive"]).read_bytes() == archive.read_bytes()
        files, metadata = packager.license_from_verified_archive(
            package, output / record["archive"], output)
        assert metadata["license"] == "MIT"
        assert len(files) == 1
        assert (output / files[0]).read_bytes() == b"Fixture license text\n"


def test_wrong_crate_checksum_fails_closed():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        package, _archive = _archive_fixture(root)
        package["checksum"] = "0" * 64
        try:
            packager.preserve_source_archive(package, root / "cargo", root / "output")
        except ValueError as error:
            assert "hash mismatch" in str(error)
        else:
            raise AssertionError("An unverified crate must never be preserved")


def test_tauri_plugin_supplemental_license_is_pinned_and_official():
    with TemporaryDirectory() as temporary:
        output = Path(temporary) / "output"
        files, source = packager.supplemental_license(
            "rust", "tauri-plugin", "2.7.2", output)
        assert len(files) == 1
        assert "tauri-apps/tauri/blob/tauri-plugin-v2.7.2/LICENSE-MIT" in source
        assert (output / files[0]).read_text(encoding="utf-8").startswith("MIT License")
        manifest = json.loads((ROOT / "desktop/packaging/license-assets/manifest.json").read_text(encoding="utf-8"))
        entry, = [item for item in manifest["licenses"]
                  if (item["kind"], item["name"], item["version"]) == ("rust", "tauri-plugin", "2.7.2")]
        assert hashlib.sha256((output / files[0]).read_bytes()).hexdigest() == entry["sha256"]
