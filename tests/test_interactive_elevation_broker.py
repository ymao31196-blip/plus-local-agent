import base64
from pathlib import Path

import pytest

import interactive_elevation_broker as broker


def _winget_request(executable: Path) -> dict:
    target = r"D:\Apps\Example App"
    return {
        "kind": "winget_install",
        "launch_id": "a" * 32,
        "executable": str(executable),
        "args": [
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
            target,
            "--silent",
        ],
        "package_id": "Vendor.Example",
        "source": "winget",
        "target_directory": target,
        "silent": True,
        "timeout_seconds": 300,
    }


def test_validate_winget_install_accepts_exact_reviewed_shape(tmp_path):
    winget = tmp_path / "winget.exe"
    winget.write_bytes(b"stub")

    result = broker._validate_request(_winget_request(winget))

    assert result["kind"] == "winget_install"
    assert result["args"][0] == "install"
    assert result["target_directory"] == r"D:\Apps\Example App"


def test_validate_winget_install_rejects_extra_argument(tmp_path):
    winget = tmp_path / "winget.exe"
    winget.write_bytes(b"stub")
    request = _winget_request(winget)
    request["args"].extend(["--override", "arbitrary"])

    with pytest.raises(ValueError, match="reviewed command shape"):
        broker._validate_request(request)


def test_validate_winget_install_rejects_non_winget_executable(tmp_path):
    fake = tmp_path / "powershell.exe"
    fake.write_bytes(b"stub")
    request = _winget_request(fake)

    with pytest.raises(ValueError, match="winget.exe"):
        broker._validate_request(request)


def test_validate_winget_install_requires_absolute_target(tmp_path):
    winget = tmp_path / "winget.exe"
    winget.write_bytes(b"stub")
    request = _winget_request(winget)
    request["target_directory"] = "relative"
    request["args"][request["args"].index("--location") + 1] = "relative"

    with pytest.raises(ValueError, match="absolute Windows path"):
        broker._validate_request(request)


def _prepare_service_control_environment(tmp_path, monkeypatch):
    powershell = (
        tmp_path
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    powershell.parent.mkdir(parents=True)
    powershell.write_bytes(b"stub")
    state_dir = tmp_path / "state"
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    monkeypatch.setattr(broker, "STATE_DIR", state_dir)
    monkeypatch.setattr(
        broker,
        "load_windows_action_policy",
        lambda: {
            "version": 1,
            "configured": True,
            "services": {
                "spooler": {
                    "service_name": "Spooler",
                    "operations": ("restart",),
                }
            },
        },
    )
    return powershell, state_dir


def test_validate_service_control_builds_fixed_elevated_command(tmp_path, monkeypatch):
    powershell, state_dir = _prepare_service_control_environment(
        tmp_path, monkeypatch
    )
    request = {
        "kind": "service_control",
        "launch_id": "b" * 32,
        "created_at": "2026-09-20T00:00:00+00:00",
        "service_name": "Spooler",
        "operation": "restart",
        "timeout_seconds": 60,
    }

    result = broker._validate_request(request)

    assert Path(result["executable"]) == powershell.resolve()
    assert result["args"][:6] == [
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Restricted",
        "-EncodedCommand",
    ]
    assert len(result["args"]) == 7
    assert result["result_path"] == str(
        (state_dir / f"{'b' * 32}.result.json").resolve()
    )
    assert result["service_name"] == "Spooler"
    assert result["operation"] == "restart"
    elevated_script = base64.b64decode(
        result["args"][-1]
    ).decode("utf-16-le")
    assert "Service is not in a stable state" in elevated_script
    assert "Service is disabled" in elevated_script
    assert "Service cannot be stopped" in elevated_script
    assert "Service has active dependent services" in elevated_script


@pytest.mark.parametrize("field,value", [
    ("service_name", "Spooler;Remove-Item"),
    ("operation", "delete"),
])
def test_validate_service_control_rejects_invalid_target(
    tmp_path, monkeypatch, field, value,
):
    _prepare_service_control_environment(tmp_path, monkeypatch)
    request = {
        "kind": "service_control",
        "launch_id": "c" * 32,
        "created_at": "2026-09-20T00:00:00+00:00",
        "service_name": "Spooler",
        "operation": "restart",
        "timeout_seconds": 60,
    }
    request[field] = value

    with pytest.raises(ValueError):
        broker._validate_request(request)


def test_validate_service_control_rejects_caller_supplied_command(
    tmp_path, monkeypatch,
):
    _prepare_service_control_environment(tmp_path, monkeypatch)
    request = {
        "kind": "service_control",
        "launch_id": "d" * 32,
        "created_at": "2026-09-20T00:00:00+00:00",
        "service_name": "Spooler",
        "operation": "restart",
        "timeout_seconds": 60,
        "executable": r"C:\Windows\System32\cmd.exe",
        "args": ["/c", "whoami"],
    }

    with pytest.raises(ValueError, match="unsupported fields"):
        broker._validate_request(request)


def test_validate_service_control_rechecks_local_policy(tmp_path, monkeypatch):
    _prepare_service_control_environment(tmp_path, monkeypatch)
    monkeypatch.setattr(
        broker,
        "load_windows_action_policy",
        lambda: {
            "version": 1,
            "configured": True,
            "services": {},
        },
    )
    request = {
        "kind": "service_control",
        "launch_id": "e" * 32,
        "created_at": "2026-09-20T00:00:00+00:00",
        "service_name": "Spooler",
        "operation": "restart",
        "timeout_seconds": 60,
    }

    with pytest.raises(ValueError, match="not authorized by local policy"):
        broker._validate_request(request)


def test_process_one_request_marks_broker_busy(tmp_path, monkeypatch):
    launch_id = "f" * 32
    monkeypatch.setattr(broker, "STATE_DIR", tmp_path)
    request_path = tmp_path / f"{launch_id}.request.json"
    status_path = tmp_path / f"{launch_id}.status.json"
    request_path.write_text("{}", encoding="utf-8")
    status_path.write_text(
        '{"state":"queued"}',
        encoding="utf-8",
    )

    events = []
    monkeypatch.setattr(
        broker,
        "_validate_request",
        lambda _value: {"launch_id": launch_id},
    )
    monkeypatch.setattr(
        broker,
        "_execute_elevated",
        lambda _request, _status_path: events.append(("execute", launch_id)),
    )
    monkeypatch.setattr(
        broker,
        "_broker_status",
        lambda state, current_launch_id=None: events.append(
            (state, current_launch_id)
        ),
    )

    assert broker._process_one_request() is True
    assert events == [
        ("running", launch_id),
        ("execute", launch_id),
        ("running", None),
    ]
