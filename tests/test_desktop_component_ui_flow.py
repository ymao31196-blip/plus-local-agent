"""Desktop component UI invariants across readable formatting and refactoring.

Real click-order and state transitions are covered in components-ux-smoke.cjs.
Keep these checks semantic: whitespace, badge component names and copy may change.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "desktop/ui/components.js").read_text(encoding="utf-8")
HTML = (ROOT / "desktop/ui/index.html").read_text(encoding="utf-8")
SMOKE = (ROOT / "desktop/verification/components-ux-smoke.cjs").read_text(encoding="utf-8")
SKILL_JS = (ROOT / "desktop/ui/skill-library-page.js").read_text(encoding="utf-8")
SKILL_SMOKE = (ROOT / "desktop/verification/skill-quick-source-smoke.cjs").read_text(encoding="utf-8")
CSS = (ROOT / "desktop/ui/style.css").read_text(encoding="utf-8")


def compact(value):
    return re.sub(r"\s+", "", value)


def has_js(fragment):
    assert compact(fragment) in compact(JS), fragment


def test_install_action_uses_fresh_sha_plan_one_confirmation_and_disable_before_repair():
    for control in ('start-install', 'preview-install', 'installation-refresh'):
        assert f'id="{control}"' in HTML
    has_js("const plan = await previewInstall({ detail: false });")
    has_js("window.confirm(installReviewText(plan, needsDisable))")
    has_js("const needsDisable = active?.lifecycle?.enabled === true")
    has_js("if (needsDisable)")
    has_js("action: 'disable', provider_id: plan.provider_id, confirmed: true")
    has_js("confirmed: true, expected_sha256: plan.sha256")
    has_js("async function installSelectedComponent()")
    assert "请先查看当前安装计划" not in JS
    assert "安装条件不满足" in JS
    assert "repairCalls[installAt].args.expected_sha256" in SMOKE


def test_missing_failed_disabled_ready_have_distinct_states_and_recovery():
    has_js("const installedFiles = row.execution_files_present === true")
    has_js("const disabled = lifecycle?.enabled === false || row.temporarily_disabled === true")
    has_js("const live = lifecycle?.state === 'ready' && !disabled && lifecycle?.enabled === true")
    has_js("const failed = !disabled && ['error', 'failed'].includes(lifecycle?.state)")
    assert "缺少运行组件" in JS
    assert "Connection closed" in SMOKE
    assert "Provider 已停用" in JS
    assert "无法读取Skill列表" in SKILL_JS
    assert "启用并连接" in JS
    assert "尚无Skill来源" in SKILL_JS
    assert "await stoppedSkill.$('reload-providers').onclick()" in SMOKE


def test_provider_badges_and_visible_enable_disable_controls():
    has_js("for (const row of providerCatalog.providers)")
    has_js("createStatusBadge(`连接：${status.connectionStatus.text}`")
    has_js("createStatusBadge(`启停：${status.enableStatus.text}`")
    has_js("handlePrimaryAction(row, 'disable', status)")
    has_js("handlePrimaryAction(row, status.primaryAction.type, status)")
    for action in ("'enable'", "'disable'", "'reload'"):
        assert action in JS
    assert ".provider-statuses" in (ROOT / "desktop/ui/provider-cards.css").read_text(encoding="utf-8")
    assert "toggleCalls[0].action" in SMOKE
    assert "toggleCalls[1].action" in SMOKE


def test_skill_library_is_single_navigation_entry_with_compact_source_management():
    assert 'data-view="skill-list">Skill Library' in HTML
    assert 'data-view="skills"' not in HTML
    assert 'id="skill-group"' not in HTML
    assert 'id="skill-operation"' not in HTML
    assert 'id="skill-open-sources"' in HTML
    assert 'id="skill-sources-panel"' in HTML
    assert 'id="skill-roots"' in HTML
    assert 'id="save-skill-permissions"' in HTML
    assert "skill-library-page.js" in HTML
    assert "async function loadSkillList()" in SKILL_JS
    assert "async function loadSkillPermissions()" in SKILL_JS
    assert "function renderSkillSources()" in SKILL_JS
    assert "if(!window.confirm(" in SKILL_JS
    assert "sourceLocationKey(kind,s.location)" in SKILL_JS
    assert "skillRpc('toggle'" in SKILL_JS
    assert "skillRpc('sync'" in SKILL_JS
    assert "skillRpc('manage'" in SKILL_JS
    assert "skillRpc('load'" in SKILL_JS
    assert "localStorage?.setItem(skillDraftKey" in SKILL_JS
    assert "localStorage?.getItem(skillDraftKey" in SKILL_JS
    assert "PASS: one entry, WebView draft" in SKILL_SMOKE
    assert "data-view=\"skills\"" in SKILL_SMOKE
    assert 'id="skill-source-add"' in HTML
    assert 'id="reload-skill-list"' in HTML


def test_bundle_is_one_click_digest_bound_and_skill_package_is_optional():
    assert 'id="start-bundle"' in HTML
    has_js("starterBundlePreview = await rpc('provider_bundle', { confirmed: false, expected_sha256: null })")
    has_js("confirmed: true, expected_sha256: starterBundlePreview.sha256")
    assert 'id="skill-package-options"' in HTML
    assert 'id="skill-package"' in HTML
    has_js("const skill_package = $('skill-package').value.trim() || null")
    has_js("skill_package: plan.skill_package_origin === 'bundled' ? null : plan.skill_package || null")
    assert "请先填写 Skill Library v0.5.0 源码目录" not in JS
    assert "请先预览常用组件安装计划" not in JS
    assert "点击「安装 / 修复」" in JS


def test_active_css_and_unique_controls_match_the_shipped_page():
    assert 'href="style.css"' in HTML
    assert 'href="provider-cards.css"' in HTML
    assert 'href="style-v2.css"' not in HTML
    assert 'id="start-install"' in HTML
    assert 'data-command="stop"' in HTML
    assert ':root' in CSS
