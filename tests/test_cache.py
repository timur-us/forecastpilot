"""Offline tests for the commentary cache — no network, no API keys."""

from types import SimpleNamespace

from app import cache, commentary


class _CountingMessages:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            content=[SimpleNamespace(text=f"Commentary #{self.calls}.")],
            usage=SimpleNamespace(input_tokens=100, output_tokens=200),
        )


class _FakeClient:
    def __init__(self):
        self.messages = _CountingMessages()


def _patched_client(monkeypatch):
    """Install one fake Anthropic client instance for the whole test."""
    fake = _FakeClient()
    monkeypatch.setattr(commentary, "Anthropic", lambda *a, **kw: fake)
    return fake


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
    cache._store.clear()
    fake = _patched_client(monkeypatch)

    first = _call()
    second = _call()

    assert first["cached"] is False
    assert second["cached"] is True
    assert second["text"] == first["text"]
    assert fake.messages.calls == 1


def test_different_language_is_a_cache_miss(monkeypatch):
    cache._store.clear()
    fake = _patched_client(monkeypatch)

    _call(language="en")
    result = _call(language="de")

    assert result["cached"] is False
    assert fake.messages.calls == 2


def test_different_ticker_is_a_cache_miss(monkeypatch):
    cache._store.clear()
    fake = _patched_client(monkeypatch)

    _call(ticker="AAPL")
    result = _call(ticker="MSFT")

    assert result["cached"] is False
    assert fake.messages.calls == 2


def test_cache_disabled_always_misses(monkeypatch):
    cache._store.clear()
    monkeypatch.setenv("COMMENTARY_CACHE_ENABLED", "false")
    fake = _patched_client(monkeypatch)

    _call()
    result = _call()

    assert result["cached"] is False
    assert fake.messages.calls == 2
