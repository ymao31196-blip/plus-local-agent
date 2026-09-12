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
