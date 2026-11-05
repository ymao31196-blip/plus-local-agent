"""Signed updater manifest and fail-closed desktop release contracts."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3

from scripts.build_signed_updater_manifest import write_manifest
from desktop_runtime.update_safety import unfinished_tasks

ROOT = Path(__file__).resolve().parents[1]


def test_release_manifest_contains_matching_url_and_exact_signature():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        installer = root / "PLA.Desktop_1.2.3_x64-setup.exe"
        signature = root / (installer.name + ".sig")
        output = root / "latest.json"
        installer.write_bytes(b"test installer fixture")
        # Fixture is intentionally not a real signature. Installation signature
        # verification is owned by Tauri, not the manifest generation script.
        signature.write_text("untrusted comment: fake test fixture\n" + "RWT" + "A" * 80 + "\n")
        result = write_manifest("1.2.3", installer, signature, output)
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert result["signed_updater"] is True
        assert payload["version"] == "1.2.3"
        assert payload["platforms"]["windows-x86_64"]["signature"] == signature.read_text().strip()
        assert payload["platforms"]["windows-x86_64"]["url"].endswith(
            "/desktop-v1.2.3/PLA.Desktop_1.2.3_x64-setup.exe"
        )


def test_release_manifest_rejects_missing_signature_and_unexpected_names():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        installer = root / "PLA.Desktop_1.2.3_x64-setup.exe"
        installer.write_bytes(b"test fixture")
        signature = root / (installer.name + ".sig")
        output = root / "latest.json"
        for bad_version in ("1.2.3-beta", "../other", "1.2"):
            try:
                write_manifest(bad_version, installer, signature, output)
            except ValueError:
                pass
            else:
                raise AssertionError("Unexpected version accepted")
        try:
            write_manifest("1.2.3", installer, signature, output)
        except ValueError:
            pass
        else:
            raise AssertionError("Unsigned installer accepted")
        assert not output.exists()


def test_update_installer_requires_stopped_services_and_compile_time_key():
    rust = (ROOT / "desktop/src-tauri/src/signed_updates.rs").read_text(encoding="utf-8")
    manager = (ROOT / "src/desktop_runtime/manager.py").read_text(encoding="utf-8")
    build = (ROOT / "desktop/packaging/build.ps1").read_text(encoding="utf-8")
    conf = json.loads((ROOT / "desktop/src-tauri/tauri.updater.conf.json").read_text(encoding="utf-8"))
    assert 'option_env!("PLA_UPDATER_PUBLIC_KEY")' in rust
    assert 'update_preflight' in rust and 'update.download(' in rust and 'update.install(bytes)' in rust
    assert rust.index('update.download(') < rust.rindex('update_preflight') < rust.index('update.install(bytes)')
    assert 'if command == "update_preflight":' in manager
    assert 'if self._installer_locks:' in manager
    assert 'TAURI_SIGNING_PRIVATE_KEY' in build
    # Tauri CLI .pub files contain Base64-encoded minisign public key text.
    # The untrusted comment and RW key line are visible only after decoding.
    assert '[Convert]::FromBase64String($pubkey)' in build
    assert 'decodedPubkey.StartsWith("untrusted comment: minisign public key")' in build
    assert '$decodedPubkey -notmatch' in build
    assert '$env:PLA_UPDATER_PUBLIC_KEY = $pubkey' in build
    assert '$updaterConfig.plugins.updater.pubkey = $pubkey' in build
    assert 'Join-Path $buildRoot "tauri.updater.generated.conf.json"' in build
    assert '@("--config", $generatedUpdaterConfig)' in build
    # A blank placeholder in the checked-in config is only safe because the
    # signed build merges the validated key into a generated config file.
    assert conf["plugins"]["updater"]["pubkey"] == ""
    assert 'Tauri bundle-type marker is missing' in build
    assert '__TAURI_BUNDLE_TYPE variable not found in binary' in build
    # Old CLI 2.8.x uses a Windows .taubndl section, incompatible with the
    # bundle token emitted by Rust tauri-utils 2.12.x. The CLI version and
    # Cargo versions must be from the same bundle-patching generation.
    package = json.loads((ROOT / "desktop/package.json").read_text(encoding="utf-8"))
    assert package["devDependencies"]["@tauri-apps/cli"] == "2.12.2"
    assert 'Incompatible Tauri CLI or stale npm lockfile' in build
    assert '$cliLocked -ne $cliRequested' in build
    # npm lockfile v3 has packages[""], which Windows PowerShell 5.1
    # ConvertFrom-Json rejects. The preflight must use Python for the lock.
    assert '$cliLocked = & $Python -c ' in build
    assert "['packages']['node_modules/@tauri-apps/cli']['version']" in build
    assert '$npmLock = Get-Content' not in build
    lock = json.loads((ROOT / "desktop/package-lock.json").read_text(encoding="utf-8"))
    assert "" in lock["packages"]
    assert lock["packages"]["node_modules/@tauri-apps/cli"]["version"] == package["devDependencies"]["@tauri-apps/cli"]
    cargo = (ROOT / "desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    assert "strip = false" in cargo
    # Limit concurrent rustc processes on low-memory Windows machines, and
    # restore the user's original environment after the build exits.
    assert '[ValidateRange(1,16)][int]$CargoJobs = 2' in build
    assert '$env:CARGO_BUILD_JOBS = [string]$CargoJobs' in build
    assert '$originalCargoBuildJobs = $env:CARGO_BUILD_JOBS' in build
    assert '$env:CARGO_BUILD_JOBS = $originalCargoBuildJobs' in build
    assert '.exe.sig' in build
    assert '--update-bundle' not in build
    assert 'Signed release requires a clean reviewed Git worktree' in build
    assert 'if (-not $LocalSignedTest)' in build
    assert 'DO_NOT_PUBLISH.txt' in build
    assert 'local-test-only' in build
    assert 'createUpdaterArtifacts' in json.dumps(conf)
    assert conf["plugins"]["updater"]["windows"]["installMode"] == "passive"
    assert "https://github.com/ymao31196-blip/plus-local-agent/releases/latest/download/latest.json" in rust
    capabilities = json.loads((ROOT / "desktop/src-tauri/capabilities/main.json").read_text(encoding="utf-8"))
    # The renderer may call our guarded command, never Tauri's raw install API.
    assert not any(item.startswith("updater:") for item in capabilities["permissions"])
    main = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
    assert "restore_autostart_after_upgrade" in main
    assert "signed_updates::is_installing()" in main
def test_updater_preflight_blocks_persisted_running_and_queued_tasks():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "tasks.sqlite3"
        assert unfinished_tasks(path) == (0, None)
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE TABLE tasks (task_id TEXT PRIMARY KEY, status TEXT NOT NULL)")
            connection.executemany("INSERT INTO tasks VALUES (?, ?)", [
                ("1", "running"), ("2", "queued"), ("3", "completed"),
            ])
            connection.commit()
        finally:
            connection.close()
        assert unfinished_tasks(path) == (2, None)
        connection = sqlite3.connect(path)
        try:
            connection.execute("UPDATE tasks SET status='completed'")
            connection.commit()
        finally:
            connection.close()
        assert unfinished_tasks(path) == (0, None)
        path.write_bytes(b"not a sqlite database")
        count, error = unfinished_tasks(path)
        assert count is None and error == "task_store_unavailable"
