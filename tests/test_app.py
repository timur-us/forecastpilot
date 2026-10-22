"""Offline tests — no network, no API keys. CI must stay green without secrets."""

import importlib

import httpx
import numpy as np
import pandas as pd
import pytest
from anthropic import AuthenticationError
from fastapi.testclient import TestClient

import app.main as app_main
from app.commentary import CommentaryError
from app.forecasting import fit_forecast
from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_root_redirects_to_docs():
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/docs"


def test_forecast_commentary_502_on_llm_error(monkeypatch, mock_prices):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def _raise(*args, **kwargs):
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx.Response(status_code=401, request=req)
        raise AuthenticationError("invalid x-api-key", response=resp, body=None)

    monkeypatch.setattr("app.main.generate_commentary", _raise)

    r = client.get("/forecast/AAPL", params={"commentary": True})
    assert r.status_code == 502
    assert r.json()["detail"] == "LLM call failed: authentication"


def test_forecast_commentary_502_on_empty_response(monkeypatch, mock_prices):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def _raise(*args, **kwargs):
        raise CommentaryError("empty response")

    monkeypatch.setattr("app.main.generate_commentary", _raise)

    r = client.get("/forecast/AAPL", params={"commentary": True})
    assert r.status_code == 502
    assert r.json()["detail"] == "LLM call failed: empty response"


def test_fit_forecast_shape_and_metrics():
    rng = np.random.default_rng(42)
    trend = np.linspace(100, 130, 300)
    series = pd.Series(trend + rng.normal(0, 1.5, 300))

    result = fit_forecast(series, horizon=30)

    assert len(result["forecast"]) == 30
    assert all(np.isfinite(result["forecast"]))
    assert "mae_model" in result["metrics"]
    assert result["metrics"]["backtest_horizon"] == 30


def test_forecast_includes_the_last_60_closes_with_dates(mock_prices):
    r = client.get("/forecast/AAPL", params={"horizon": 5})

    body = r.json()
    history = body["history"]
    assert r.status_code == 200
    assert len(history) == 60
    assert all(set(point) == {"date", "close"} for point in history)
    assert history[-1] == {"date": "2026-10-21", "close": body["last_close"]}
    assert history[0]["date"] == mock_prices.index[-60].strftime("%Y-%m-%d")
    assert [p["date"] for p in history] == sorted(p["date"] for p in history)


def test_cors_is_off_without_configured_origins(mock_prices):
    r = client.get("/health", headers={"Origin": "https://timur-us.github.io"})

    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


@pytest.fixture
def cors_client(monkeypatch):
    """The app as deployed: CORS_ORIGINS set before the module is imported."""
    # The reload re-runs load_dotenv(); keep a local .env out of the test.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: True)
    monkeypatch.setenv(
        "CORS_ORIGINS", "https://timur-us.github.io/, http://localhost:8080"
    )
    module = importlib.reload(app_main)
    yield TestClient(module.app)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    importlib.reload(app_main)


def test_cors_origins_are_normalised_and_validated(monkeypatch):
    monkeypatch.setenv(
        "CORS_ORIGINS", " https://timur-us.github.io/ ,http://localhost:8080"
    )
    assert app_main._cors_origins() == [
        "https://timur-us.github.io",
        "http://localhost:8080",
    ]

    for bad in ("*", "timur-us.github.io", "https://timur-us.github.io/forecastpilot/"):
        monkeypatch.setenv("CORS_ORIGINS", bad)
        with pytest.raises(ValueError):
            app_main._cors_origins()


def test_cors_allows_the_configured_origin_and_exposes_limit_headers(
    cors_client, mock_prices
):
    origin = "https://timur-us.github.io"

    preflight = cors_client.options(
        "/forecast/NVDA",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )
    r = cors_client.get("/forecast/NVDA", headers={"Origin": origin})

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin
    exposed = r.headers["access-control-expose-headers"].lower()
    assert "x-ratelimit-remaining" in exposed and "retry-after" in exposed


def test_cors_rejects_other_origins(cors_client, mock_prices):
    r = cors_client.get("/forecast/NVDA", headers={"Origin": "https://evil.example"})

    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers
