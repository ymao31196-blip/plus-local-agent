"""Login startup and read-only update discovery release contracts."""
from pathlib import Path
import json

from desktop_runtime.updates import check_stable_release

ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size):
        return self.payload[:size]


def fake_api(payload):
    def open_url(request, timeout):
        assert request.full_url == (
            "https://api.github.com/repos/ymao31196-blip/plus-local-agent/releases/latest"
        )
        assert timeout <= 8
        return FakeResponse(payload)
    return open_url


def release(version="1.1.0", *, draft=False, prerelease=False, assets=True):
    return {
        "tag_name": "desktop-v" + version,
        "draft": draft,
        "prerelease": prerelease,
        "assets": [{"name": f"PLA.Desktop_{version}_x64-setup.exe"}] if assets else [],
    }


def test_update_check_only_accepts_newer_stable_releases():
    result = check_stable_release("1.0.0", open_url=fake_api(release()))
    assert result["status"] == "available_manual"
    assert result["automatic_install"] is False
    assert result["latest_version"] == "1.1.0"
    assert check_stable_release("1.1.0", open_url=fake_api(release()))["status"] == "latest"
    assert check_stable_release("1.0.0", open_url=fake_api(release("1.1.0", prerelease=True)))["status"] == "invalid_release"
    assert check_stable_release("1.0.0", open_url=fake_api(release("1.1.0", draft=True)))["status"] == "invalid_release"
    assert check_stable_release("1.0.0", open_url=fake_api(release(assets=False)))["status"] == "pending_installer"
    assert check_stable_release("1.1.0", open_url=fake_api(release("1.0.9")))["status"] == "latest"


def test_update_check_never_trusts_unstructured_release_tags():
    payload = release()
    payload["tag_name"] = "v1.2.0"
    assert check_stable_release("1.0.0", open_url=fake_api(payload))["status"] == "invalid_release"
    payload["tag_name"] = "desktop-v1.2.0-malicious"
    assert check_stable_release("1.0.0", open_url=fake_api(payload))["status"] == "invalid_release"


def test_win_login_startup_requires_opt_in_and_uses_background_mode():
    source = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
    bundle = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    uninstall = (ROOT / "desktop/packaging/installer-hooks.nsh").read_text(encoding="utf-8")
    assert 'format!("{legacy_registration} --autostart")' in source
    assert 'arg == "--autostart"' in source
    assert 'status["config"]["autostart"] == true' in source
    assert 'bridge.call("start", json!({}))' in source
    assert 'bridge.call("connect", json!({}))' in source
    assert 'window.hide()' in source
    assert "autostart_status" in source
    assert bundle["app"]["windows"][0]["visible"] is False
    assert " --autostart'" in uninstall


def test_verification_smoke_mocks_payloadless_native_autostart_status():
    smoke = (ROOT / "desktop/verification/verification-status-smoke.cjs").read_text(encoding="utf-8")
    app = (ROOT / "desktop/ui/app.js").read_text(encoding="utf-8")
    assert "await invoke('autostart_status')" in app
    assert "if(method==='autostart_status') return false;" in smoke
    assert "const {command,args} = request || {};" in smoke
    assert "if(method!=='manage') throw" in smoke
    # Native commands must not be destructured as though every call were manage().
    assert "async (method,{command,args})" not in smoke


def test_release_ui_truthfully_distinguishes_update_discovery_from_install():
    app = (ROOT / "desktop/ui/app.js").read_text(encoding="utf-8")
    html = (ROOT / "desktop/ui/index.html").read_text(encoding="utf-8")
    assert "update_check" in app and "checkDesktopUpdate" in app
    assert "automatic_install" not in app
    assert "安装已签名更新" in html
    assert "signed_update_check" in app and "signed_update_install" in app
    assert "update_preflight" in app
    assert "未签名构建只提供新版提示" in html
