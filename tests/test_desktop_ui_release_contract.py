"""Release-facing UI checks that survive the V2 layout refactor.

These guard real resource loading, native CDP selectors and readable status
labels. Node/Playwright and frozen WebView2 remain separate execution gates.
"""
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import json
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "desktop/ui"
HTML = (UI / "index.html").read_text(encoding="utf-8")
APP = (UI / "app.js").read_text(encoding="utf-8")
COMP = (UI / "components.js").read_text(encoding="utf-8")
SKILL_UI = (UI / "skill-library-page.js").read_text(encoding="utf-8")
NATIVE = (ROOT / "desktop/verification/native-ui.cjs").read_text(encoding="utf-8")


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.assets = []
        self.commands = []
        self.sections = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if d.get("id"):
            self.ids.append(d["id"])
        if tag == "section":
            self.sections.append(d.get("id"))
        if tag == "button" and d.get("data-command"):
            self.commands.append((self.sections[-1] if self.sections else "", d["data-command"]))
        if tag in ("script", "link"):
            ref = d.get("src") or d.get("href")
            if ref:
                self.assets.append(ref)

    def handle_endtag(self, tag):
        if tag == "section" and self.sections:
            self.sections.pop()


def test_live_assets_exist_and_are_not_untracked_v2_duplicates():
    p = Elements()
    p.feed(HTML)
    assert not [name for name, count in Counter(p.ids).items() if count > 1]
    assert set(p.assets) == {"style.css", "provider-cards.css", "component-manager.css",
                             "app.js", "components.js", "component-manager.js", "skill-library-page.js", "program-permissions.js"}
    assert all((UI / asset).is_file() for asset in p.assets)
    assert not [f for f in UI.glob("*-old.*") if f.is_file()]
    assert not [f for f in UI.glob("*-v2.*") if f.is_file()]


def test_tauri_bundle_explicitly_embeds_current_platform_icons():
    config = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    expected = ["icons/32x32.png", "icons/128x128.png", "icons/128x128@2x.png",
                "icons/icon.icns", "icons/icon.ico"]
    assert config["bundle"]["icon"] == expected
    icon_root = ROOT / "desktop/src-tauri"
    assert all((icon_root / name).is_file() for name in expected)
    ico = (icon_root / "icons/icon.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", ico[:6])
    assert reserved == 0 and kind == 1 and count >= 7
    sizes = set()
    for index in range(count):
        width, height = struct.unpack("<BB", ico[6 + index * 16:8 + index * 16])
        sizes.add((width or 256, height or 256))
    assert {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)} <= sizes
    build = (ROOT / "desktop/packaging/build.ps1").read_text(encoding="utf-8-sig")
    assert "cargo.exe clean --package pla-desktop" in build
    assert build.index("Clean PLA Desktop native bundle resources") < build.index("Tauri NSIS installer")
    assert config["bundle"]["resources"]["icons/icon.ico"] == "pla-desktop.ico"
    hooks = (ROOT / "desktop/packaging/installer-hooks.nsh").read_text(encoding="utf-8")
    assert 'CreateShortCut "$DESKTOP\\PLA Desktop.lnk"' in hooks
    assert '$INSTDIR\\resources\\pla-desktop.ico' in hooks


def test_web_api_settings_are_explicit_opt_in_and_token_is_session_scoped():
    p = Elements()
    p.feed(HTML)
    required = {
        "web-api-panel", "web-api-enabled", "web-api-origins", "web-api-roots",
        "web-api-tool-list-directory", "web-api-tool-read-text",
        "web-api-tool-search-text", "web-api-tool-run-process",
        "save-web-api", "test-web-api", "web-api-test-state", "show-web-api-token", "web-api-endpoint",
        "web-api-token", "copy-web-api-token", "hide-web-api-token",
    }
    assert required <= set(p.ids)
    assert "web_api_token" in APP
    assert "web_api_test" in APP
    assert "'test-web-api'" in APP
    assert '"web_api_test"' in (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
    assert "navigator.clipboard.writeText(token)" in APP
    assert "web_api_enabled: $('web-api-enabled').checked" in APP
    assert "run_process" in APP
    assert "127.0.0.1" in HTML
    assert "默认关闭" in HTML


def test_native_runtime_badges_and_service_controls_really_exist():
    p = Elements()
    p.feed(HTML)
    assert p.commands.count(("setup", "stop")) == 1
    assert p.commands.count(("setup", "start")) == 1
    assert "#runtime-status" in NATIVE
    assert "document.querySelector('#runtime')" not in NATIVE
    assert "runtime-status" in p.ids
    assert "setup: '首次配置与连接'" in APP
    assert 'nav [data-view="about"]' in NATIVE
    assert "await page.locator('#quit-app').click()" in NATIVE
    assert "createStatusBadge(`连接：${status.connectionStatus.text}`" in COMP
    assert "createStatusBadge(`启停：${status.enableStatus.text}`" in COMP


def test_all_top_level_dom_bindings_resolve_or_are_created_before_access():
    p = Elements()
    p.feed(HTML)
    refs = set(re.findall(r"\$\(['\"]([^'\"]+)['\"]\)", APP + COMP + SKILL_UI))
    absent = refs - set(p.ids)
    # These two are created at startup as part of the optional legacy detector.
    assert absent == {"legacy-root", "detect-legacy"} or not absent


def test_component_aware_activity_ui_controls_and_safe_rendering():
    p = Elements()
    p.feed(HTML)
    assert {"activity-list", "activity-filter", "activity-more"} <= set(p.ids)
    assert "activity.capability" in APP and "activity.provider" in APP
    assert "activity.program" in APP
    assert "tool.textContent = activityConversationLabel(activity.conversation_number)" in APP
    assert "+ ' · ' + description.title" in APP
    assert "'来源对话'" in APP
    assert "content.textContent = String(value)" in APP
    assert "ACTIVITY_PREVIEW_COUNT = 6" in APP


def _luminance(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def test_primary_buttons_have_readable_white_text():
    css = (UI / "style.css").read_text(encoding="utf-8")
    color = re.search(r"--color-brand:\s*#([0-9a-fA-F]{6})", css)
    assert color is not None
    v = color.group(1)
    rgb = tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    assert 1.05 / (_luminance(rgb) + 0.05) >= 4.5



def test_conversation_activity_is_compact_and_accessibly_labeled():
    p = Elements()
    p.feed(HTML)
    assert {"conversation-summary", "conversation-count", "conversation-identities",
            "conversation-recent", "conversation-unknown", "conversation-period"} <= set(p.ids)
    assert 'role="group" aria-label="最近120秒ChatGPT工具活动' in HTML
    assert "renderConversationSummary(conversations)" in APP
    assert "renderConversationSummary(null)" in APP
    assert "conversation_number" in APP
    assert "Number.isSafeInteger(number)" in APP
    assert "chip.textContent = activityConversationLabel(number)" in APP
    assert "conversations.recently_stopped_count" in APP
    assert "conversations.recently_stopped" in APP
    assert "recent.hidden = false" in APP
    assert "近60秒自动识别的 ChatGPT 对话：" not in APP
    css = (UI / "style.css").read_text(encoding="utf-8")
    assert ".conversation-strip" in css
    assert ".conversation-primary strong" in css
    assert ".conversation-chip" in css
    assert ".conversation-chip.is-recent" in css
    assert ".conversation-recent[hidden]" in css
