"""Forecasting core — pure functions, no network, fully unit-testable."""

import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing


def fit_forecast(series: pd.Series, horizon: int = 30) -> dict:
    """Holt-Winters forecast with a naive-baseline backtest.

    Returns {'forecast': list[float], 'metrics': dict}.
    Model choice is deliberately simple for v0.1 — swap in ARIMA/Prophet later
    and let the backtest decide which one earns its place.
    """
    series = series.astype(float).reset_index(drop=True)
    metrics = _backtest(series, horizon)

    model = ExponentialSmoothing(
        series, trend="add", seasonal=None, initialization_method="estimated"
    )
    fitted = model.fit(optimized=True)
    forecast = fitted.forecast(horizon)
    return {"forecast": [float(v) for v in forecast], "metrics": metrics}


def _backtest(series: pd.Series, horizon: int) -> dict:
    """Hold out the last `horizon` points; compare model vs naive last-value forecast."""
    if len(series) <= horizon * 2:
        return {"note": "series too short for backtest"}
    train, test = series[:-horizon], series[-horizon:]

    model = ExponentialSmoothing(
        train, trend="add", seasonal=None, initialization_method="estimated"
    )
    pred = model.fit(optimized=True).forecast(horizon)
    naive = np.repeat(train.iloc[-1], horizon)

    return {
        "backtest_horizon": horizon,
        "mae_model": _mae(test, pred),
        "mae_naive": _mae(test, naive),
        "mape_model_pct": _mape(test, pred),
        "beats_naive": bool(_mae(test, pred) < _mae(test, naive)),
    }


def _mae(actual, pred) -> float:
    return float(np.mean(np.abs(np.asarray(actual) - np.asarray(pred))).round(4))


def _mape(actual, pred) -> float:
    actual, pred = np.asarray(actual), np.asarray(pred)
    mask = actual != 0
    return float(
        (np.mean(np.abs((actual[mask] - pred[mask]) / actual[mask])) * 100).round(2)
    )
