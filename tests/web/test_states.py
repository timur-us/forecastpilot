"""Every state of the web app in headless Chromium, against the mock API.

Each test fails on an uncaught page error, on a console error (except the
"Failed to load resource" line Chromium logs for an expected 429 or 502) and
on any CSP violation, seen both in the console and by a listener installed
before the page loads. Every state is screenshotted into the artifacts dir.
"""

import math
import re
from pathlib import Path

import pytest
from playwright.sync_api import BrowserContext, Page, expect

PHONE = {"width": 390, "height": 844}

# Runs before any page script; CSP does not apply to init scripts.
CSP_LISTENER = """
window.__csp = [];
document.addEventListener('securitypolicyviolation', (e) => {
  window.__csp.push(e.violatedDirective + ' blocked ' + (e.blockedURI || 'inline'));
}, true);
"""


class App:
    """The page under test plus everything it logged."""

    def __init__(self, page: Page, url: str, shots: Path):
        self.page = page
        self.url = url
        self.shots = shots
        self.console: list[tuple[str, str]] = []
        self.page_errors: list[str] = []
        page.on("console", lambda m: self.console.append((m.type, m.text)))
        page.on("pageerror", lambda e: self.page_errors.append(str(e)))

    def open(self) -> None:
        self.page.goto(self.url)
        expect(self.page.locator("#state-idle")).to_be_visible()

    def request(self, ticker: str, commentary: bool = True) -> None:
        self.page.locator("#ticker").fill(ticker)
        self.page.locator("#commentary").set_checked(commentary)
        self.page.get_by_role("button", name="Get forecast").click()

    def wait_for_result(self) -> None:
        expect(self.page.locator("#state-result")).to_be_visible(timeout=15_000)

    def shot(self, name: str) -> None:
        self.page.screenshot(path=self.shots / f"{name}.png", full_page=True)

    def assert_no_horizontal_scroll(self) -> None:
        scroll, inner = self.page.evaluate(
            "[document.documentElement.scrollWidth, window.innerWidth]"
        )
        assert scroll <= inner, f"page is {scroll} px wide in a {inner} px viewport"

    def assert_clean(self, *failed_statuses: int) -> None:
        """No page error, no CSP violation, no console error but the expected."""
        assert self.page_errors == []
        expected = [
            re.compile(
                rf"^Failed to load resource: the server responded with a status of {s}\b"
            )
            for s in failed_statuses
        ]
        errors = [
            text
            for kind, text in self.console
            if kind == "error" and not any(p.search(text) for p in expected)
        ]
        assert errors == []
        assert [t for _, t in self.console if "Content Security Policy" in t] == []
        assert self.page.evaluate("window.__csp") == []
        for p in expected:  # each expected failure did happen
            assert any(p.search(t) for k, t in self.console if k == "error"), p.pattern


@pytest.fixture
def app(page: Page, context: BrowserContext, app_url: str, artifacts: Path) -> App:
    context.add_init_script(CSP_LISTENER)
    return App(page, app_url, artifacts)


def test_start_shows_the_form_with_the_ticker_focused(app):
    page = app.page
    app.open()
    expect(page.locator("#ticker")).to_be_focused()
    for state in ("#state-waking", "#state-result", "#state-429", "#error"):
        expect(page.locator(state)).to_be_hidden()
    app.shot("desktop-01-start")
    app.assert_clean()


def test_waking_shows_phase_one_until_health_answers(app, api):
    page = app.page
    api.health_delay = 2.5  # a cold container: /health answers late
    app.open()
    app.request("NVDA")
    expect(page.locator("#state-waking")).to_be_visible()
    expect(page.locator("#waking-title")).to_have_text("Waking the service…")
    expect(page.locator("#waking-tag")).to_have_text("NVDA · 30 trading days")
    first, second = page.locator("#steps .fp-step").all()
    expect(first).to_have_attribute("data-state", "active")
    expect(first.locator(".fp-step-label")).to_have_text("Starting the container")
    expect(second).to_have_attribute("data-state", "pending")
    expect(page.locator("#progress-caption")).to_contain_text(
        "Phase 1 of 2 · Starting the container"
    )
    app.shot("desktop-02-waking")
    app.wait_for_result()
    app.assert_clean()


def test_result_shows_sections_verdict_check_and_quota(app, nvda):
    page = app.page
    app.open()
    app.request("NVDA")
    app.wait_for_result()
    comm = nvda["commentary"]
    ev = comm["eval"]
    expect(page.locator("#comm-text h4")).to_have_text(
        ["Price movement", "Model quality", "Interpretation"]
    )
    expect(page.locator("#comm-text section").first).to_contain_text(
        comm["sections"]["price_movement"]
    )
    expect(page.locator("#verdict-title")).to_have_text("Beats the naive baseline")
    expect(page.locator("#check-big")).to_have_text(
        f"{ev['numbers_matched']} / {ev['numbers_checked']}"
    )
    expect(page.locator("#check-chip-text")).to_have_text("all numbers traced")
    expect(page.locator("#quota-text")).to_have_text("4 of 5 left this hour")
    expect(page.locator("#disclaimer")).to_be_visible()
    expect(page.locator("#disclaimer")).to_have_text(comm["sections"]["disclaimer"])
    expect(page.locator("#callout")).to_be_hidden()
    expect(page.locator("#comm-text .fp-cut")).to_have_count(0)
    expect(page.locator("#k-last")).to_have_text("221.97")
    expect(page.locator("#k-fc")).to_have_text("226.34")
    expect(page.locator("#x-left")).to_have_text("60 trading days ago")
    expect(page.locator("#r-generated")).to_have_text("2026-10-25 12:29 UTC")
    app.shot("desktop-03-result")
    app.assert_clean()


def test_reply_cut_at_the_token_budget_shows_the_commentary_error(app, api):
    page = app.page
    app.open()
    app.request("CUT")
    expect(page.locator("#error")).to_be_visible(timeout=15_000)
    expect(page.locator("#error-title")).to_have_text(
        "The commentary could not be generated."
    )
    expect(page.locator("#error-text")).to_contain_text(api.CUT_DETAIL)
    expect(page.locator("#state-idle")).to_be_visible()
    app.shot("desktop-04-truncated-error")
    app.assert_clean(502)


def test_legacy_truncated_commentary_shows_the_callout(app):
    page = app.page
    app.open()
    app.request("LEGACY")
    app.wait_for_result()
    expect(page.locator("#callout")).to_be_visible()
    expect(page.locator("#comm-text .fp-cut")).to_have_count(1)
    expect(page.locator("#comm-text section").last.locator(".fp-cut")).to_have_count(1)
    expect(page.locator("#dd-trunc")).to_have_text("yes")
    expect(page.locator("#dd-stop")).to_have_text("max_tokens")
    app.shot("desktop-04-truncated-legacy")
    app.assert_clean()


def test_no_backtest_shows_no_verdict(app):
    page = app.page
    app.open()
    app.request("NOBT", commentary=False)
    app.wait_for_result()
    expect(page.locator("#verdict-title")).to_have_text("No backtest for this horizon")
    expect(page.locator("#bar")).to_be_hidden()
    expect(page.locator("#note-nobacktest")).to_be_visible()
    expect(page.locator("#k-mae")).to_have_text("–")
    expect(page.locator("#comm")).to_be_hidden()
    expect(page.locator("#quota-text")).to_have_text("29 of 30 left this hour")
    app.shot("desktop-06-no-backtest")
    app.assert_clean()


def test_rate_limit_shows_the_429_state_and_the_way_out(app, api):
    page = app.page
    app.open()
    app.request("LIMIT")
    expect(page.locator("#state-429")).to_be_visible(timeout=15_000)
    expect(page.locator("#limit-title")).to_have_text(
        "You have used your 5 commentary requests for this hour"
    )
    minutes = math.ceil(api.LIMIT_RESET_S / 60)
    expect(page.locator("#limit-text")).to_contain_text(
        f"Your limit resets in {minutes} minutes."
    )
    button = page.get_by_role("button", name="Get the forecast without commentary")
    expect(button).to_be_visible()
    app.shot("desktop-05-ratelimit")
    button.click()
    app.wait_for_result()
    expect(page.locator("#comm")).to_be_hidden()
    expect(page.locator("#quota-text")).to_have_text("29 of 30 left this hour")
    app.shot("desktop-05-ratelimit-then-plain")
    app.assert_clean(429)


@pytest.mark.browser_context_args(viewport=PHONE)
def test_phone_layout_never_scrolls_sideways(app):
    page = app.page
    app.open()
    app.assert_no_horizontal_scroll()
    app.shot("phone-01-start")
    app.request("NVDA")
    app.wait_for_result()
    app.assert_no_horizontal_scroll()
    app.shot("phone-03-result")
    page.get_by_role("button", name="New forecast").click()
    app.request("LIMIT")
    expect(page.locator("#state-429")).to_be_visible(timeout=15_000)
    app.assert_no_horizontal_scroll()
    app.shot("phone-05-ratelimit")
    assert page.evaluate("window.innerWidth") == PHONE["width"]
    app.assert_clean(429)


def test_a_csp_violation_would_be_caught(app):
    """The checks above can fail: a request the CSP blocks is seen twice."""
    app.open()
    with app.page.expect_console_message(lambda m: "Content Security Policy" in m.text):
        app.page.evaluate("fetch('http://192.0.2.1/').catch(() => null)")
    app.page.wait_for_function("window.__csp.length > 0")
    assert app.page.evaluate("window.__csp") == [
        "connect-src blocked http://192.0.2.1/"
    ]
    assert app.page_errors == []
