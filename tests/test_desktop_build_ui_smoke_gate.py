"""RC.8 installer build must block on the real component UI smoke test.

The UI regression test is intentionally a separate Node script (mock DOM);
this Python contract verifies the Windows build cannot silently skip it.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_smoke_exercises_live_install_state_not_old_started_copy():
    smoke = (ROOT / "desktop/verification/components-ux-smoke.cjs").read_text(encoding="utf-8")
    assert "await h.$('start-install').onclick()" in smoke
    assert "/docx.+正在安装.*PID 123/" in smoke
    assert "assert.match(h.$('install-live').textContent, /安装任务已启动/)" not in smoke
    assert "assert.equal(installed[1].args.expected_sha256, 'reviewed-plan')" in smoke
    assert "assert.ok(disableAt >= 0 && disableAt < installAt" in smoke


def test_release_build_runs_smoke_as_blocking_preflight_before_nsis():
    build = (ROOT / "desktop/packaging/build.ps1").read_text(encoding="utf-8")
    guard = 'Invoke-LoggedNative "Component UI smoke"'
    assert guard in build
    assert "components-ux-smoke.cjs" in build
    assert "components-ux-smoke.log" in build
    assert build.index(guard) < build.index('Invoke-LoggedNative "Frozen Runtime"')
    assert build.index(guard) < build.index('Invoke-LoggedNative "Tauri NSIS installer"')
    assert "if (-not $SkipAcceptance)" in build
