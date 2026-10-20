"""Release-facing UI checks that survive the V2 layout refactor.

These guard real resource loading, native CDP selectors and readable status
labels. Node/Playwright and frozen WebView2 remain separate execution gates.
"""
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import re

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
                             "app.js", "components.js", "component-manager.js", "skill-library-page.js"}
    assert all((UI / asset).is_file() for asset in p.assets)
    assert not [f for f in UI.glob("*-old.*") if f.is_file()]
    assert not [f for f in UI.glob("*-v2.*") if f.is_file()]


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
