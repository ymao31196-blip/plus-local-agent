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
    assert "else if(failed && installedFiles)" in js


def test_bundle_also_one_click_and_skill_source_still_required():
    root = Path(__file__).resolve().parents[1]
    js = (root / "desktop/ui/components.js").read_text(encoding="utf-8")
    html = (root / "desktop/ui/index.html").read_text(encoding="utf-8")
    assert 'id="start-bundle" class="primary">准备常用组件' in html
    assert "starterBundlePreview=await rpc('provider_bundle',{confirmed:false,expected_sha256:null})" in js
    assert "if(!starterBundlePreview)throw '请先预览常用组件安装计划。'" not in js
    assert "if(provider_id==='skill-library'&&!skill_package)throw" in js
    assert "点击「安装 / 修复」" in js
