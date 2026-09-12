import json
import pytest

from providers import software_migration_server as migration


def _entry(**updates):
    value = {
        "display_name": "Example App",
        "DisplayVersion": "1.2.3",
        "Publisher": "Example Corp",
        "InstallLocation": r"C:\Program Files\Example App",
        "UninstallString": r'"C:\Program Files\Example App\uninstall.exe"',
        "QuietUninstallString": None,
        "EstimatedSize": 102400,
        "InstallDate": "20260912",
        "SystemComponent": 0,
        "WindowsInstaller": 0,
        "registry_source": "HKLM",
        "registry_subkey": "Example App",
    }
    value.update(updates)
    return value


def test_assessment_prefers_reinstall_for_normal_program_files_app(monkeypatch):
    monkeypatch.setattr(migration, "_find_uninstall_matches", lambda _query: [_entry()])

    result = migration._assess_software_migration("Example App", r"D:\Apps")

    assert result["classification"] == "reinstall_preferred"
    assert result["software"]["current_drive"] == "C:"
    assert result["software"]["estimated_size_mb"] == 100.0
    assert result["requires_admin_likely"] is True
    assert result["execution_performed"] is False


def test_assessment_reports_already_on_target(monkeypatch):
    monkeypatch.setattr(
        migration,
        "_find_uninstall_matches",
        lambda _query: [_entry(InstallLocation=r"D:\Apps\Example App")],
    )

    result = migration._assess_software_migration("Example App", r"D:\Apps")

    assert result["classification"] == "already_on_target"


def test_assessment_blocks_system_managed_runtime(monkeypatch):
    monkeypatch.setattr(
        migration,
        "_find_uninstall_matches",
        lambda _query: [
            _entry(
                display_name="Microsoft Edge WebView2 Runtime",
                Publisher="Microsoft Corporation",
                InstallLocation=r"C:\Program Files (x86)\Microsoft\EdgeWebView",
            )
        ],
    )

    result = migration._assess_software_migration(
        "Microsoft Edge WebView2 Runtime",
        r"D:\Apps",
    )

    assert result["classification"] == "do_not_migrate"
    assert result["system_managed_reasons"]


def test_assessment_requires_unambiguous_match(monkeypatch):
    monkeypatch.setattr(
        migration,
        "_find_uninstall_matches",
        lambda _query: [_entry(display_name="App A"), _entry(display_name="App B")],
    )

    result = migration._assess_software_migration("App", r"D:\Apps")

    assert result["status"] == "ambiguous"
    assert len(result["matches"]) == 2


def test_preview_winget_reinstall_is_non_executing_and_uses_location():
    result = migration._preview_winget_reinstall(
        "Vendor.App",
        r"D:\Apps\Vendor App",
    )

    assert result["execution_performed"] is False
    assert result["argv"] == [
        "winget",
        "install",
        "--id",
        "Vendor.App",
        "--exact",
        "--source",
        "winget",
        "--location",
        r"D:\Apps\Vendor App",
        "--silent",
    ]


def test_preview_rejects_unsafe_package_identifier():
    with pytest.raises(ValueError, match="package_id"):
        migration._preview_winget_reinstall(
            "--override",
            r"D:\Apps\Bad",
        )


def test_target_path_must_be_absolute():
    with pytest.raises(ValueError, match="absolute Windows path"):
        migration._preview_winget_reinstall(
            "Vendor.App",
            "relative-path",
        )


def test_prepare_migration_returns_snapshot_and_orchestration(monkeypatch):
    entry = _entry()
    monkeypatch.setattr(migration, "_find_uninstall_matches", lambda _query: [entry])

    result = migration._prepare_migration(
        "Example App",
        "Vendor.Example",
        r"D:\Apps\Example App",
    )

    assert result["status"] == "prepared"
    assert result["execution_performed"] is False
    assert result["snapshot"]["install_location"] == r"C:\Program Files\Example App"
    assert len(result["snapshot_sha256"]) == 64
    assert [step["step"] for step in result["orchestration"]] == [
        "preflight",
        "uninstall",
        "install",
        "verify",
    ]
    assert result["orchestration"][1]["requires_confirmation"] is True
    assert result["orchestration"][2]["requires_confirmation"] is True


def test_snapshot_hash_changes_when_installation_changes():
    first = migration._snapshot_sha256(_entry(DisplayVersion="1.0"))
    second = migration._snapshot_sha256(_entry(DisplayVersion="2.0"))

    assert first != second


def test_execute_winget_install_refuses_while_old_install_exists(monkeypatch):
    monkeypatch.setattr(migration, "_find_uninstall_matches", lambda _query: [_entry()])

    called = {"value": False}

    def fake_run(*_args, **_kwargs):
        called["value"] = True
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(migration.subprocess, "run", fake_run)

    result = migration._execute_winget_install(
        "Vendor.Example",
        "Example App",
        r"D:\Apps\Example App",
    )

    assert result["status"] == "precondition_failed"
    assert result["execution_performed"] is False
    assert called["value"] is False


def test_execute_winget_install_uses_reviewed_argv(monkeypatch):
    monkeypatch.setattr(migration, "_find_uninstall_matches", lambda _query: [])
    monkeypatch.setattr(
        migration.shutil,
        "which",
        lambda name: r"C:\Windows\winget.exe" if name == "winget" else None,
    )

    captured = {}

    class Completed:
        returncode = 0
        stdout = b"installed"
        stderr = b""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(migration.subprocess, "run", fake_run)

    result = migration._execute_winget_install(
        "Vendor.Example",
        "Example App",
        r"D:\Apps\Example App",
    )

    assert result["status"] == "completed"
    assert result["execution_performed"] is True
    assert captured["argv"] == [
        r"C:\Windows\winget.exe",
        "install",
        "--id",
        "Vendor.Example",
        "--exact",
        "--source",
        "winget",
        "--accept-source-agreements",
        "--accept-package-agreements",
        "--disable-interactivity",
        "--location",
        r"D:\Apps\Example App",
        "--silent",
    ]
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["check"] is False


def test_verify_migration_passes_when_location_is_under_target(monkeypatch):
    monkeypatch.setattr(
        migration,
        "_find_uninstall_matches",
        lambda _query: [_entry(InstallLocation=r"D:\Apps\Example App\bin")],
    )

    result = migration._verify_migration(
        "Example App",
        r"D:\Apps\Example App",
    )

    assert result["status"] == "passed"
    assert result["verified"] is True


def test_verify_migration_fails_when_installer_ignores_target(monkeypatch):
    monkeypatch.setattr(
        migration,
        "_find_uninstall_matches",
        lambda _query: [_entry(InstallLocation=r"C:\Program Files\Example App")],
    )

    result = migration._verify_migration(
        "Example App",
        r"D:\Apps\Example App",
    )

    assert result["status"] == "failed"
    assert result["verified"] is False


def test_execute_winget_uninstall_uses_exact_silent_noninteractive_command(monkeypatch):
    captured = {}
    calls = {"count": 0}

    class Completed:
        returncode = 0
        stdout = b"uninstalled"
        stderr = b""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return Completed()

    def fake_find(_query):
        calls["count"] += 1
        return [_entry()] if calls["count"] == 1 else []

    monkeypatch.setattr(migration, "_find_uninstall_matches", fake_find)
    monkeypatch.setattr(
        migration.shutil,
        "which",
        lambda name: r"C:\Windows\winget.exe" if name == "winget" else None,
    )
    monkeypatch.setattr(migration.subprocess, "run", fake_run)

    result = migration._execute_winget_uninstall(
        "Vendor.Example",
        "Example App",
    )

    assert result["status"] == "completed"
    assert result["execution_performed"] is True
    assert captured["argv"] == [
        r"C:\Windows\winget.exe",
        "uninstall",
        "--id",
        "Vendor.Example",
        "--exact",
        "--source",
        "winget",
        "--accept-source-agreements",
        "--disable-interactivity",
        "--silent",
    ]
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["check"] is False


def test_execute_winget_uninstall_refuses_ambiguous_install(monkeypatch):
    monkeypatch.setattr(
        migration,
        "_find_uninstall_matches",
        lambda _query: [_entry(display_name="App A"), _entry(display_name="App B")],
    )

    result = migration._execute_winget_uninstall(
        "Vendor.Example",
        "Example",
    )

    assert result["status"] == "precondition_failed"
    assert result["execution_performed"] is False


def test_execute_winget_uninstall_noops_when_not_installed(monkeypatch):
    monkeypatch.setattr(migration, "_find_uninstall_matches", lambda _query: [])

    result = migration._execute_winget_uninstall(
        "Vendor.Example",
        "Example",
    )

    assert result["status"] == "noop"
    assert result["execution_performed"] is False


def test_registered_uninstaller_executes_registered_executable_with_explicit_args(
    monkeypatch,
    tmp_path,
):
    exe = tmp_path / "helper.exe"
    exe.write_bytes(b"stub")
    entry = _entry(UninstallString=str(exe), QuietUninstallString=None)
    calls = {"find": 0}
    captured = {}

    def fake_find(_query):
        calls["find"] += 1
        return [entry] if calls["find"] == 1 else []

    class Completed:
        returncode = 0
        stdout = b"removed"
        stderr = b""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(migration, "_find_uninstall_matches", fake_find)
    monkeypatch.setattr(migration.subprocess, "run", fake_run)

    result = migration._execute_registered_uninstaller(
        "Example App",
        extra_args=["/S"],
    )

    assert result["status"] == "completed"
    assert result["execution_performed"] is True
    assert captured["argv"] == [str(exe.resolve()), "/S"]
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["check"] is False


def test_registered_uninstaller_refuses_ambiguous_match(monkeypatch):
    monkeypatch.setattr(
        migration,
        "_find_uninstall_matches",
        lambda _query: [_entry(display_name="A"), _entry(display_name="B")],
    )

    result = migration._execute_registered_uninstaller(
        "Example",
        extra_args=["/S"],
    )

    assert result["status"] == "precondition_failed"
    assert result["execution_performed"] is False


def test_registered_uninstaller_rejects_invalid_extra_arg(monkeypatch, tmp_path):
    exe = tmp_path / "helper.exe"
    exe.write_bytes(b"stub")
    monkeypatch.setattr(
        migration,
        "_find_uninstall_matches",
        lambda _query: [_entry(UninstallString=str(exe))],
    )

    with pytest.raises(ValueError, match="extra_args"):
        migration._execute_registered_uninstaller(
            "Example App",
            extra_args=[""],
        )


def test_registered_uninstaller_elevation_returns_external_pending(monkeypatch, tmp_path):
    exe = tmp_path / "helper.exe"
    exe.write_bytes(b"stub")
    monkeypatch.setattr(
        migration,
        "_find_uninstall_matches",
        lambda _query: [_entry(UninstallString=str(exe))],
    )
    captured = {}

    def fake_launch(executable, args, software_query, timeout_seconds):
        captured.update(
            executable=executable,
            args=args,
            software_query=software_query,
            timeout_seconds=timeout_seconds,
        )
        return {
            "status": "external_pending",
            "execution_performed": True,
            "elevation_requested": True,
            "launch_id": "a" * 32,
        }

    monkeypatch.setattr(migration, "_launch_elevated_uninstaller", fake_launch)

    result = migration._execute_registered_uninstaller(
        "Example App",
        extra_args=["/S"],
        timeout_seconds=120,
        elevate=True,
    )

    assert result["status"] == "external_pending"
    assert result["launch_id"] == "a" * 32
    assert captured["executable"] == str(exe.resolve())
    assert captured["args"] == ["/S"]
    assert captured["software_query"] == "Example App"
    assert captured["timeout_seconds"] == 120


def test_elevated_uninstall_status_reports_completed_when_registry_entry_is_gone(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(migration, "_ELEVATION_STATE_DIR", tmp_path)
    launch_id = "b" * 32
    (tmp_path / f"{launch_id}.request.json").write_text(
        '{"software_query":"Example App"}',
        encoding="utf-8",
    )
    (tmp_path / f"{launch_id}.status.json").write_text(
        '{"state":"completed","returncode":0,"created_at":"x","updated_at":"y"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(migration, "_find_uninstall_matches", lambda _query: [])

    result = migration._elevated_uninstall_status(launch_id)

    assert result["status"] == "completed"
    assert result["worker_state"] == "completed"
    assert result["remaining_matches"] == []


def test_elevated_uninstall_status_stays_pending_while_worker_waits(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(migration, "_ELEVATION_STATE_DIR", tmp_path)
    launch_id = "c" * 32
    (tmp_path / f"{launch_id}.request.json").write_text(
        '{"software_query":"Example App"}',
        encoding="utf-8",
    )
    (tmp_path / f"{launch_id}.status.json").write_text(
        '{"state":"requesting_elevation","returncode":null}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        migration,
        "_find_uninstall_matches",
        lambda _query: [_entry()],
    )

    result = migration._elevated_uninstall_status(launch_id)

    assert result["status"] == "external_pending"
    assert result["worker_state"] == "requesting_elevation"


def test_elevation_broker_status_missing_is_not_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(
        migration,
        "_BROKER_STATUS_PATH",
        tmp_path / "broker_status.json",
    )

    result = migration._elevation_broker_status()

    assert result["status"] == "not_ready"
    assert result["state"] == "missing"


def test_launch_elevated_uninstaller_refuses_when_broker_not_ready(monkeypatch):
    monkeypatch.setattr(
        migration,
        "_elevation_broker_status",
        lambda: {
            "status": "not_ready",
            "state": "missing",
            "pid": None,
        },
    )

    result = migration._launch_elevated_uninstaller(
        r"C:\Program Files\Example App\uninstall.exe",
        ["/S"],
        "Example App",
        120,
    )

    assert result["status"] == "not_ready"
    assert result["execution_performed"] is False
    assert result["elevation_requested"] is False


def test_launch_elevated_uninstaller_queues_only_for_ready_broker(
    monkeypatch,
    tmp_path,
):
    class FakeUuid:
        hex = "d" * 32

    monkeypatch.setattr(migration, "_ELEVATION_STATE_DIR", tmp_path)
    monkeypatch.setattr(migration, "uuid4", lambda: FakeUuid())
    monkeypatch.setattr(
        migration,
        "_elevation_broker_status",
        lambda: {
            "status": "ready",
            "state": "running",
            "pid": 1234,
        },
    )

    result = migration._launch_elevated_uninstaller(
        r"C:\Program Files\Example App\uninstall.exe",
        ["/S"],
        "Example App",
        120,
    )

    request = json.loads(
        (tmp_path / f'{"d" * 32}.request.json').read_text(encoding="utf-8")
    )
    status = json.loads(
        (tmp_path / f'{"d" * 32}.status.json').read_text(encoding="utf-8")
    )

    assert result["status"] == "external_pending"
    assert result["broker_pid"] == 1234
    assert request["kind"] == "registered_uninstaller"
    assert request["executable"] == r"C:\Program Files\Example App\uninstall.exe"
    assert request["args"] == ["/S"]
    assert status["state"] == "queued"
    assert status["broker_pid"] == 1234


def test_launch_elevated_winget_install_queues_reviewed_request(
    monkeypatch,
    tmp_path,
):
    class FakeUuid:
        hex = "e" * 32

    monkeypatch.setattr(migration, "_ELEVATION_STATE_DIR", tmp_path)
    monkeypatch.setattr(migration, "uuid4", lambda: FakeUuid())
    monkeypatch.setattr(
        migration,
        "_elevation_broker_status",
        lambda: {"status": "ready", "state": "running", "pid": 4321},
    )
    monkeypatch.setattr(migration, "_find_uninstall_matches", lambda _query: [])
    monkeypatch.setattr(
        migration.shutil,
        "which",
        lambda name: r"C:\Users\User\AppData\Local\Microsoft\WindowsApps\winget.exe"
        if name == "winget"
        else None,
    )

    result = migration._launch_elevated_winget_install(
        "Vendor.Example",
        "Example App",
        r"D:\Apps\Example App",
        timeout_seconds=180,
    )

    request = json.loads(
        (tmp_path / f'{"e" * 32}.request.json').read_text(encoding="utf-8")
    )
    assert result["status"] == "external_pending"
    assert result["broker_pid"] == 4321
    assert request["kind"] == "winget_install"
    assert request["package_id"] == "Vendor.Example"
    assert request["target_directory"] == r"D:\Apps\Example App"
    assert request["args"] == [
        "install",
        "--id",
        "Vendor.Example",
        "--exact",
        "--source",
        "winget",
        "--accept-source-agreements",
        "--accept-package-agreements",
        "--disable-interactivity",
        "--location",
        r"D:\Apps\Example App",
        "--silent",
    ]


def test_elevated_install_status_requires_verified_target(monkeypatch, tmp_path):
    monkeypatch.setattr(migration, "_ELEVATION_STATE_DIR", tmp_path)
    launch_id = "f" * 32
    (tmp_path / f"{launch_id}.request.json").write_text(
        json.dumps(
            {
                "kind": "winget_install",
                "software_query": "Example App",
                "target_directory": r"D:\Apps\Example App",
                "package_id": "Vendor.Example",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / f"{launch_id}.status.json").write_text(
        json.dumps({"state": "completed", "returncode": 0}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        migration,
        "_verify_migration",
        lambda _query, _target: {"status": "passed", "verified": True},
    )

    result = migration._elevated_install_status(launch_id)

    assert result["status"] == "completed"
    assert result["verification"]["verified"] is True

