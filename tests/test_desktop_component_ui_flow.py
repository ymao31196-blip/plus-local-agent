"""RC.8+ component installation UI: one review/confirmation, no repeated preview gate."""
from pathlib import Path


def test_install_main_action_performs_fresh_digest_preview_and_confirmation():
    root = Path(__file__).resolve().parents[1]
    js = (root / "desktop/ui/components.js").read_text(encoding="utf-8")
    html = (root / "desktop/ui/index.html").read_text(encoding="utf-8")
    assert 'id="start-install" class="primary">安装 / 修复' in html
    assert 'id="preview-install">查看依赖详情（可选）' in html
    assert "const plan=await previewInstall({detail:false})" in js
    assert "window.confirm(installReviewText(plan,needsDisable))" in js
    assert "const needsDisable=active?.lifecycle?.enabled===true" in js
    assert "if(needsDisable)" in js
    assert "action:'disable',provider_id:plan.provider_id,confirmed:true" in js
    assert "confirmed:true,expected_sha256:plan.sha256" in js
    assert "async function installSelectedComponent()" in js
    assert "if(!installPreview)throw '请先查看当前安装计划。'" not in js
    assert "安装条件不满足" in js


def test_failed_provider_distinguishable_from_missing_runtime():
    root = Path(__file__).resolve().parents[1]
    js = (root / "desktop/ui/components.js").read_text(encoding="utf-8")
    assert "error:'连接失败'" in js
    assert "const installedFiles=row.execution_files_present===true" in js
    assert "lifecycle?.error_message||'连接未建立'" in js
    assert "quick.append(button('重试连接'" in js
    assert "quick.append(button('修复依赖'" in js
    assert "if(failed && installedFiles)" in js


def test_skill_disabled_ready_does_not_masquerade_as_live_and_shows_recovery():
    root = Path(__file__).resolve().parents[1]
    js = (root / "desktop/ui/components.js").read_text(encoding="utf-8")
    smoke = (root / "desktop/verification/components-ux-smoke.cjs").read_text(encoding="utf-8")
    assert "lifecycle?.enabled===false||row.temporarily_disabled===true" in js
    assert "lifecycle?.state==='ready'&&!disabled&&lifecycle?.enabled===true" in js
    assert "disabled?'未连接'" in js
    assert "Provider已停用；历史工具发现记录不代表当前可调用" in js
    assert "Capability is unavailable: skill-library." in js
    assert "请前往「MCP插件与Provider」点击「启用并连接」" in js
    assert "if(installed)" in js and "providerCatalog.providers.findIndex" in js
    assert "await stoppedSkill.$('reload-providers').onclick()" in smoke
    assert "await stoppedSkill.$('reload-skill-list').onclick()" in smoke
    assert "temporarily_disabled:true" in smoke


def test_every_provider_card_shows_separate_enablement_and_connection_with_quick_toggle():
    root = Path(__file__).resolve().parents[1]
    js = (root / "desktop/ui/components.js").read_text(encoding="utf-8")
    css = (root / "desktop/ui/style.css").read_text(encoding="utf-8")
    smoke = (root / "desktop/verification/components-ux-smoke.cjs").read_text(encoding="utf-8")
    assert "for(const row of providerCatalog.providers)" in js
    assert "node('div',undefined,'provider-statuses')" in js
    assert "启停：${enableLabel}" in js
    assert "连接：${currentStatus}" in js
    assert "provider-enable-status ${enableClass}" in js
    assert "quick.append(button('停用'" in js
    assert "quick.append(button('启用并连接'" in js
    assert "action:'disable',provider_id:row.provider_id,confirmed:true" in js
    assert "action:'enable',provider_id:row.provider_id,confirmed:true" in js
    assert ".provider-statuses" in css
    assert ".provider-enable-status.is-enabled" in css
    assert ".provider-enable-status.is-disabled" in css
    assert "const toggleCard=()" in smoke
    assert "toggleCalls[0].action,'disable'" in smoke
    assert "toggleCalls[1].action,'enable'" in smoke


def test_connected_empty_skill_library_explains_missing_source():
    root = Path(__file__).resolve().parents[1]
    js = (root / "desktop/ui/components.js").read_text(encoding="utf-8")
    smoke = (root / "desktop/verification/components-ux-smoke.cjs").read_text(encoding="utf-8")
    assert "Skill Library已连接，但尚未添加Skill来源" in js
    assert "安装Skill Library程序不会自动导入个人Skill" in js
    assert "await emptySkill.$('reload-skill-list').onclick()" in smoke


def test_bundle_also_one_click_and_skill_source_still_required():
    root = Path(__file__).resolve().parents[1]
    js = (root / "desktop/ui/components.js").read_text(encoding="utf-8")
    html = (root / "desktop/ui/index.html").read_text(encoding="utf-8")
    assert 'id="start-bundle" class="primary">准备常用组件' in html
    assert "starterBundlePreview=await rpc('provider_bundle',{confirmed:false,expected_sha256:null})" in js
    assert "if(!starterBundlePreview)throw '请先预览常用组件安装计划。'" not in js
    assert "if(provider_id==='skill-library'&&!skill_package)throw" in js
    assert "点击「安装 / 修复」" in js
