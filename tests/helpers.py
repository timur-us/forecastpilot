"""Shared offline test data — no network, no API keys."""

import numpy as np
import pandas as pd


def price_series(
    seed: int = 7, points: int = 300, start: float = 50.0, end: float = 60.0
):
    """A synthetic daily close series indexed by trading days, like yfinance."""
    rng = np.random.default_rng(seed)
    trend = np.linspace(start, end, points)
    index = pd.bdate_range(end="2026-10-21", periods=points)
    return pd.Series(trend + rng.normal(0, 1.0, points), index=index)
