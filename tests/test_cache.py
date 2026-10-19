"""Offline tests for the commentary cache — no network, no API keys."""

import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import cache, commentary


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    cache._store.clear()
    monkeypatch.setenv("COMMENTARY_CACHE_ENABLED", "true")
    yield
    cache._store.clear()


class _CountingMessages:
    def __init__(self, text=None):
        self.calls = 0
        self.text = text

    def create(self, **kwargs):
        self.calls += 1
        text = self.text or f"Commentary #{self.calls}."
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=100, output_tokens=200),
        )


class _FakeClient:
    def __init__(self, text=None):
        self.messages = _CountingMessages(text)


def _patched_client(monkeypatch, text=None):
    """Install one fake Anthropic client instance for the whole test."""
    fake = _FakeClient(text)
    monkeypatch.setattr(commentary, "Anthropic", lambda *a, **kw: fake)
    return fake


class _FrozenDatetime:
    """Stand-in for commentary.datetime with a clock the test controls."""

    current = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.current


def _call(**overrides):
    kwargs = {
        "ticker": "AAPL",
        "metrics": {},
        "last_close": 100.0,
        "forecast": [100.0, 101.0],
        "language": "en",
    }
    kwargs.update(overrides)
    return commentary.generate_commentary(**kwargs)


def test_second_identical_request_is_served_from_cache(monkeypatch):
    fake = _patched_client(monkeypatch)

    first = _call()
    second = _call()

    assert first["cached"] is False
    assert second["cached"] is True
    assert second["text"] == first["text"]
    assert fake.messages.calls == 1


def test_different_language_is_a_cache_miss(monkeypatch):
    fake = _patched_client(monkeypatch)

    _call(language="en")
    result = _call(language="de")

    assert result["cached"] is False
    assert fake.messages.calls == 2


def test_different_ticker_is_a_cache_miss(monkeypatch):
    fake = _patched_client(monkeypatch)

    _call(ticker="AAPL")
    result = _call(ticker="MSFT")

    assert result["cached"] is False
    assert fake.messages.calls == 2


def test_changed_last_close_is_a_cache_miss(monkeypatch):
    fake = _patched_client(monkeypatch)

    _call(last_close=100.0)
    result = _call(last_close=100.13)

    assert result["cached"] is False
    assert fake.messages.calls == 2


def test_changed_forecast_start_is_a_cache_miss(monkeypatch):
    fake = _patched_client(monkeypatch)

    _call(forecast=[100.0, 101.0])
    result = _call(forecast=[100.5, 101.0])

    assert result["cached"] is False
    assert fake.messages.calls == 2


def test_changed_forecast_end_is_a_cache_miss(monkeypatch):
    fake = _patched_client(monkeypatch)

    _call(forecast=[100.0, 101.0])
    result = _call(forecast=[100.0, 101.5])

    assert result["cached"] is False
    assert fake.messages.calls == 2


def test_sub_cent_change_is_a_cache_hit(monkeypatch):
    # The prompt prints two decimals, so differences below a cent do not
    # change the numbers the text cites.
    fake = _patched_client(monkeypatch)

    _call(last_close=100.001, forecast=[100.001, 101.001])
    result = _call(last_close=100.004, forecast=[100.004, 101.004])

    assert result["cached"] is True
    assert result["eval"]["passed"] is True
    assert fake.messages.calls == 1


def test_hit_re_evaluates_numbers_against_current_metrics(monkeypatch):
    _patched_client(monkeypatch, text="Model MAE 1.0000.")

    # Forecast 100 -> 103 (not 101): a delta of 1.0 would make "1.0000"
    # an allowed number on its own and hide the metric change.
    first = _call(forecast=[100.0, 103.0], metrics={"mae_model": 1.0})
    second = _call(forecast=[100.0, 103.0], metrics={"mae_model": 7.0})

    assert first["eval"]["passed"] is True
    assert second["cached"] is True
    assert second["eval"]["passed"] is False
    assert second["eval"]["unmatched"] == ["1.0000"]


def test_generated_at_is_set_and_kept_on_hit(monkeypatch):
    _patched_client(monkeypatch)
    monkeypatch.setattr(commentary, "datetime", _FrozenDatetime)

    _FrozenDatetime.current = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
    first = _call()
    _FrozenDatetime.current = datetime(2026, 10, 20, 12, 0, 5, tzinfo=timezone.utc)
    second = _call()

    assert first["generated_at"] == "2026-10-20T12:00:00+00:00"
    assert second["cached"] is True
    assert second["generated_at"] == "2026-10-20T12:00:00+00:00"


def test_set_drops_expired_entries():
    stale_key = ("OLD", "en", 2, ("1.00", "1.00", "2.00"))
    cache._store[stale_key] = ({"text": "old"}, time.time() - 1)

    cache.set("AAPL", "en", 2, ("100.00", "100.00", "101.00"), {"text": "new"})

    assert stale_key not in cache._store
    assert cache.get("AAPL", "en", 2, ("100.00", "100.00", "101.00")) == {"text": "new"}


def test_cache_disabled_always_misses(monkeypatch):
    monkeypatch.setenv("COMMENTARY_CACHE_ENABLED", "false")
    fake = _patched_client(monkeypatch)

    _call()
    result = _call()

    assert result["cached"] is False
    assert fake.messages.calls == 2
