"""Shared offline fixtures — no network, no API keys."""

import pytest

from tests.helpers import price_series


@pytest.fixture
def mock_prices(monkeypatch):
    """Serve a synthetic series instead of downloading market data."""
    series = price_series()
    monkeypatch.setattr("app.main._load_close_series", lambda ticker: series)
    return series
