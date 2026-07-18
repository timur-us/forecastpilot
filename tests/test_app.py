"""Offline tests — no network, no API keys. CI must stay green without secrets."""

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from app.forecasting import fit_forecast
from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_fit_forecast_shape_and_metrics():
    rng = np.random.default_rng(42)
    trend = np.linspace(100, 130, 300)
    series = pd.Series(trend + rng.normal(0, 1.5, 300))

    result = fit_forecast(series, horizon=30)

    assert len(result["forecast"]) == 30
    assert all(np.isfinite(result["forecast"]))
    assert "mae_model" in result["metrics"]
    assert result["metrics"]["backtest_horizon"] == 30
