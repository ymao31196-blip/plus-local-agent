"""Native UI Playwright selectors must remain unambiguous in the shipped HTML.

The prior RC.8 native acceptance stopped after three actual PASS checks because
[data-command="stop"] matched three buttons in different UI sections.
"""
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "desktop/ui/index.html").read_text(encoding="utf-8")
E2E = (ROOT / "desktop/verification/native-ui.cjs").read_text(encoding="utf-8")


class Commands(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sections = []
        self.buttons = []
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        if tag == "section":
            self.sections.append(attrs.get("id"))
        if tag == "button" and "data-command" in attrs:
            self.buttons.append((self.sections[-1] if self.sections else "", attrs["data-command"]))

    def handle_endtag(self, tag):
        if tag == "section" and self.sections:
            self.sections.pop()


def test_native_e2e_never_uses_global_ambiguous_command_selectors():
    html = Commands()
    html.feed(HTML)
    global_counts = Counter(command for _, command in html.buttons)
    raw = re.findall(r"page\.locator\('([^']+)'\)", E2E)
    for selector in raw:
        m = re.fullmatch(r'\[data-command="([^"]+)"\]', selector)
        if m:
            assert global_counts[m.group(1)] == 1, (
                f"Native E2E global selector {selector!r} is ambiguous")
        scoped = re.fullmatch(r'#([^ ]+) \[data-command="([^"]+)"\]', selector)
        if scoped:
            assert html.buttons.count(scoped.groups()) == 1, (
                f"Native E2E scoped selector {selector!r} is not unique")
    assert '#setup [data-command="stop"]' in raw
    assert '#setup [data-command="start"]' in raw


def test_native_e2e_id_selectors_exist_once_in_html():
    html = Commands()
    html.feed(HTML)
    counts = Counter(html.ids)
    # Includes IDs within the HTML view and the selectors embedded in nav.
    ids = set(re.findall(r"page\.locator\('#([a-zA-Z0-9-]+)'\)", E2E))
    assert ids
    for id_ in ids:
        assert counts[id_] == 1, f"Native E2E ID #{id_} matches {counts[id_]} nodes"
