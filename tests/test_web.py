"""Static checks for the web UI — the page must stay CSP-clean and self-contained."""

import re
from html.parser import HTMLParser
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
API_ORIGIN = (
    "https://forecastpilot.mangotree-f644cfcc.germanywestcentral.azurecontainerapps.io"
)


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.style_attrs = 0
        self.inline_handlers = []
        self.inline_blocks = []
        self.csp = None
        self.local_refs = []
        self.external = []
        self._in_block = None

    def handle_endtag(self, tag):
        if tag == self._in_block:
            self._in_block = None

    def handle_data(self, data):
        if self._in_block and data.strip():
            self.inline_blocks.append(self._in_block)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style") and not a.get("src"):
            self._in_block = tag
        if "style" in a:
            self.style_attrs += 1
        self.inline_handlers += [k for k in a if k.startswith("on")]
        if (
            tag == "meta"
            and a.get("http-equiv", "").lower() == "content-security-policy"
        ):
            self.csp = a.get("content", "")
        for key in ("src", "href"):
            v = a.get(key)
            if not v or v.startswith("#"):
                continue
            if v.startswith(("http://", "https://")):
                self.external.append((tag, v))
            else:
                self.local_refs.append(v)


def _scan():
    s = _Scan()
    s.feed((WEB / "index.html").read_text(encoding="utf-8"))
    return s


def test_markup_has_no_inline_styles_or_handlers():
    s = _scan()
    assert s.style_attrs == 0
    assert s.inline_handlers == []
    assert s.inline_blocks == []  # no inline <script> or <style> blocks either


def test_csp_allows_only_self_and_the_api():
    csp = _scan().csp
    assert csp and "default-src 'self'" in csp
    connect = re.search(r"connect-src ([^;]+)", csp).group(1).split()
    assert API_ORIGIN in connect
    assert all(
        src in ("'self'", API_ORIGIN)
        or src.startswith(("http://127.0.0.1", "http://localhost"))
        for src in connect
    )
    assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp


def test_external_references_are_links_only():
    # No scripts, stylesheets, fonts or images from other hosts.
    assert all(tag == "a" for tag, _ in _scan().external)


def test_local_assets_exist():
    for ref in _scan().local_refs:
        assert (WEB / ref.split("?")[0]).is_file(), ref
    for font in (
        "Inter-latin.woff2",
        "JetBrainsMono-latin.woff2",
        "OFL-Inter.txt",
        "OFL-JetBrainsMono.txt",
    ):
        assert (WEB / "fonts" / font).is_file()


def test_app_js_targets_the_api_and_avoids_html_injection():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert f"const API_BASE = '{API_ORIGIN}'" in js
    assert not re.search(
        r"innerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function", js
    )
    assert not re.search(r"setAttribute\(\s*['\"]style['\"]", js)


def test_stylesheet_self_hosts_the_fonts():
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in css and "fonts.gstatic.com" not in css
    assert (
        "url('fonts/Inter-latin.woff2')" in css
        and "url('fonts/JetBrainsMono-latin.woff2')" in css
    )
    assert "font-display: swap" in css
