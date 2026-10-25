"""Browser run of the web app: web/ served statically, plus a mock API.

Both servers are stdlib ThreadingHTTPServers on 127.0.0.1 with a free port,
started once per session. The page is opened at
http://127.0.0.1:<static>/?api=http://127.0.0.1:<api>, the local override
app.js accepts. The mock API answers from tests/web/fixtures/nvda_v3.json, a
real v3 response that tests/test_web_fixture.py keeps in step with app/.

Nothing here imports app/: these tests run in their own environment
(tests/web/requirements.txt). Where Playwright is missing, as in the main
suite's environment, the tests in this directory are not collected.
"""

import copy
import functools
import json
import os
import threading
import time
from http.server import (
    BaseHTTPRequestHandler,
    SimpleHTTPRequestHandler,
    ThreadingHTTPServer,
)
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

try:
    import playwright.sync_api  # noqa: F401
    import pytest_playwright  # noqa: F401
except ImportError:
    collect_ignore_glob = ["test_*.py"]

HERE = Path(__file__).resolve().parent
WEB = HERE.parents[1] / "web"
FIXTURE = HERE / "fixtures" / "nvda_v3.json"

DESKTOP = {"width": 1280, "height": 800}

RATE_LIMIT_HEADERS = (
    "X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset, Retry-After"
)


class MockApi:
    """What the mock API answers; tests change it through the `api` fixture."""

    CUT_DETAIL = "LLM call failed: incomplete structured reply (stop_reason=max_tokens)"
    LIMIT_RESET_S = 2460  # 41 minutes, as the page rounds up
    NO_BACKTEST_NOTE = "series too short for backtest"

    def __init__(self, body: dict):
        self.body = body
        self.url = ""
        self.health_delay = 0.0

    def reset(self) -> None:
        self.health_delay = 0.0

    def forecast(self, ticker: str, with_commentary: bool):
        """(status, body, rate-limit headers) for GET /forecast/<ticker>."""
        now = int(time.time())
        if ticker == "LIMIT" and with_commentary:
            reset = now + self.LIMIT_RESET_S
            return (
                429,
                {"detail": "Rate limit exceeded: 5 per 1 hour"},
                _limits(5, 0, reset, retry_after=self.LIMIT_RESET_S),
            )
        if ticker == "CUT" and with_commentary:
            # What app.main answers when call_model raises CommentaryError.
            return 502, {"detail": self.CUT_DETAIL}, {}
        if ticker not in ("NVDA", "NOBT", "CUT", "LEGACY", "LIMIT"):
            return 404, {"detail": f"No data found for ticker '{ticker}'"}, {}

        body = copy.deepcopy(self.body)
        body["ticker"] = ticker
        if ticker == "NOBT":
            # A series shorter than twice the horizon: no backtest, and no
            # commentary in the fixture that would match it.
            body["metrics"] = {"note": self.NO_BACKTEST_NOTE}
            with_commentary = False
        elif ticker == "LEGACY" and with_commentary:
            body["commentary"] = _legacy_truncated(body["commentary"])
        if not with_commentary:
            body["commentary"] = None
        limit = 5 if with_commentary else 30
        return 200, body, _limits(limit, limit - 1, now + 3600)


def _limits(limit: int, remaining: int, reset: int, retry_after=None) -> dict:
    """slowapi's headers; X-RateLimit-Reset is a Unix timestamp."""
    headers = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(reset),
    }
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return headers


def _legacy_truncated(block: dict) -> dict:
    """A free-text block cut at the token budget, as prompts before v3 returned.

    No `sections`, the text stops mid-way through the last part and has no
    disclaimer. The cut falls after the last number, so `eval` still holds.
    """
    parts = block["text"].split("\n\n")
    last = parts[2]
    text = "\n\n".join(parts[:2] + [last[: len(last) // 2].rstrip()])
    legacy = {k: v for k, v in block.items() if k != "sections"}
    legacy.update(
        text=text,
        prompt_version="2026-10-25.2",
        stop_reason="max_tokens",
        truncated=True,
    )
    return legacy


class _ApiHandler(BaseHTTPRequestHandler):
    server_version = "MockForecastPilot/1"

    def log_message(self, *args):  # keep the test output clean
        pass

    def _cors(self) -> None:
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Expose-Headers", RATE_LIMIT_HEADERS)

    def _json(self, status: int, body, headers=None) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self._cors()
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET")
        requested = self.headers.get("Access-Control-Request-Headers")
        if requested:
            self.send_header("Access-Control-Allow-Headers", requested)
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        api: MockApi = self.server.api
        url = urlsplit(self.path)
        if url.path == "/health":
            # A sleeping container answers late, not with an error.
            time.sleep(api.health_delay)
            return self._json(200, {"status": "ok"})
        if url.path.startswith("/forecast/"):
            ticker = url.path.removeprefix("/forecast/").upper()
            query = parse_qs(url.query)
            with_commentary = query.get("commentary", ["false"])[0] == "true"
            status, body, headers = api.forecast(ticker, with_commentary)
            return self._json(status, body, headers)
        return self._json(404, {"detail": "Not Found"})


class _StaticHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".svg": "image/svg+xml",
        ".woff2": "font/woff2",
        ".txt": "text/plain; charset=utf-8",
    }

    def log_message(self, *args):
        pass


@functools.cache
def _fixture_body() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _serve(handler, name: str):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name=name, daemon=True)
    thread.start()
    return server, thread


def _stop(server, thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def static_url():
    """Base URL of web/, served as GitHub Pages would."""
    handler = functools.partial(_StaticHandler, directory=str(WEB))
    server, thread = _serve(handler, "web-static")
    yield f"http://127.0.0.1:{server.server_address[1]}"
    _stop(server, thread)


@pytest.fixture(scope="session")
def _mock_api():
    api = MockApi(_fixture_body())
    server, thread = _serve(_ApiHandler, "mock-api")
    server.api = api
    api.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield api
    _stop(server, thread)


@pytest.fixture
def api(_mock_api):
    """The mock API, reset before and after each test."""
    _mock_api.reset()
    yield _mock_api
    _mock_api.reset()


@pytest.fixture(scope="session")
def nvda() -> dict:
    """The fixture response the mock API serves for NVDA."""
    return copy.deepcopy(_fixture_body())


@pytest.fixture
def app_url(static_url, api) -> str:
    return f"{static_url}/?api={api.url}"


@pytest.fixture(scope="session")
def artifacts() -> Path:
    """Where screenshots go; WEB_ARTIFACTS_DIR overrides tests/web/artifacts/."""
    path = Path(os.environ.get("WEB_ARTIFACTS_DIR") or HERE / "artifacts")
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Desktop by default; a test can override it with the marker."""
    return {**browser_context_args, "viewport": DESKTOP, "device_scale_factor": 1}
