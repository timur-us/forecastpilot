"""Offline tests — no network, no API keys. CI must stay green without secrets."""

import httpx
import numpy as np
import pandas as pd
from anthropic import AuthenticationError
from fastapi.testclient import TestClient

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


def test_forecast_commentary_502_on_llm_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def _raise(*args, **kwargs):
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx.Response(status_code=401, request=req)
        raise AuthenticationError("invalid x-api-key", response=resp, body=None)

    monkeypatch.setattr("app.main.generate_commentary", _raise)

    rng = np.random.default_rng(7)
    trend = np.linspace(50, 60, 300)
    series = pd.Series(trend + rng.normal(0, 1.0, 300))
    monkeypatch.setattr("app.main._load_close_series", lambda ticker: series)

    r = client.get("/forecast/AAPL", params={"commentary": True})
    assert r.status_code == 502
    assert r.json()["detail"] == "LLM call failed: authentication"


def test_fit_forecast_shape_and_metrics():
    rng = np.random.default_rng(42)
    trend = np.linspace(100, 130, 300)
    series = pd.Series(trend + rng.normal(0, 1.5, 300))

    result = fit_forecast(series, horizon=30)

    assert len(result["forecast"]) == 30
    assert all(np.isfinite(result["forecast"]))
    assert "mae_model" in result["metrics"]
    assert result["metrics"]["backtest_horizon"] == 30
