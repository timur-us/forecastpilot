"""Offline tests for the /forecast rate limits — no network, no API keys.

Every RATE_LIMIT_* value below is unique across this file (and unused by
any other test). slowapi's storage key is derived only from the parsed
limit (amount/granularity) plus client+endpoint — not from which
decorator or env var set it — so two limits sharing the same string
would share the same counter bucket even if one governs plain forecasts
and the other commentary.
"""

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _mock_series(monkeypatch):
    rng = np.random.default_rng(11)
    trend = np.linspace(50, 60, 300)
    series = pd.Series(trend + rng.normal(0, 1.0, 300))
    monkeypatch.setattr("app.main._load_close_series", lambda ticker: series)


def test_commentary_rate_limit_returns_429(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_COMMENTARY", "2/hour")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _mock_series(monkeypatch)

    params = {"commentary": True}
    allowed = [client.get("/forecast/AAPL", params=params) for _ in range(2)]
    blocked = client.get("/forecast/AAPL", params=params)

    # Under the limit: no key configured, so requests fail fast at 503
    # (never reaching the Anthropic call) without tripping the limiter.
    assert all(r.status_code == 503 for r in allowed)
    assert blocked.status_code == 429
    assert "detail" in blocked.json()


def test_plain_forecast_limit_is_independent_of_commentary_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_FORECAST", "4/hour")
    monkeypatch.setenv("RATE_LIMIT_COMMENTARY", "1/hour")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _mock_series(monkeypatch)

    # Exhaust the (separate) commentary bucket first.
    exhausted = client.get("/forecast/AAPL", params={"commentary": True})
    over_commentary_limit = client.get("/forecast/AAPL", params={"commentary": True})
    assert exhausted.status_code == 503
    assert over_commentary_limit.status_code == 429

    # Plain forecasts (no commentary) still have their own untouched budget.
    plain = [client.get("/forecast/AAPL") for _ in range(2)]
    assert all(r.status_code == 200 for r in plain)


def test_commentary_limit_is_per_caller_behind_trusted_proxy(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_COMMENTARY", "3/hour")
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "100.64.0.0/10")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _mock_series(monkeypatch)
    # Every request arrives from the ingress; X-Forwarded-For names the caller.
    proxied = TestClient(app, client=("100.100.0.9", 40000))
    params = {"commentary": True}
    caller_a = {"X-Forwarded-For": "203.0.113.5"}
    caller_b = {"X-Forwarded-For": "198.51.100.7"}
    spoofed_by_a = {"X-Forwarded-For": "198.51.100.7, 203.0.113.5"}

    allowed = [
        proxied.get("/forecast/AAPL", params=params, headers=caller_a) for _ in range(3)
    ]
    blocked = proxied.get("/forecast/AAPL", params=params, headers=caller_a)
    still_blocked = proxied.get("/forecast/AAPL", params=params, headers=spoofed_by_a)
    other = proxied.get("/forecast/AAPL", params=params, headers=caller_b)

    assert all(r.status_code == 503 for r in allowed)
    assert blocked.status_code == 429
    assert still_blocked.status_code == 429  # the ingress-appended address wins
    assert other.status_code == 503  # a different caller has its own budget
