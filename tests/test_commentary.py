"""Offline tests for app.commentary — no network, no API keys."""

from types import SimpleNamespace

from app import commentary


class _FakeMessages:
    def create(self, **kwargs):
        return SimpleNamespace(
            content=[SimpleNamespace(text="Fake commentary. Last close 100.00.")],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=2000),
        )


class _FakeClient:
    def __init__(self, *args, **kwargs):
        self.messages = _FakeMessages()


def test_cost_usd_uses_default_pricing(monkeypatch):
    monkeypatch.setattr(commentary, "Anthropic", _FakeClient)
    monkeypatch.delenv("ANTHROPIC_PRICE_INPUT_PER_MTOK", raising=False)
    monkeypatch.delenv("ANTHROPIC_PRICE_OUTPUT_PER_MTOK", raising=False)

    result = commentary.generate_commentary(
        ticker="AAPL",
        metrics={"mae_model": 1.0},
        last_close=100.0,
        forecast=[100.0, 101.0],
    )

    # 1000/1e6 * 3.0 + 2000/1e6 * 15.0 = 0.003 + 0.03
    assert result["cost_usd"] == 0.033


def test_cost_usd_respects_env_pricing(monkeypatch):
    monkeypatch.setattr(commentary, "Anthropic", _FakeClient)
    monkeypatch.setenv("ANTHROPIC_PRICE_INPUT_PER_MTOK", "10")
    monkeypatch.setenv("ANTHROPIC_PRICE_OUTPUT_PER_MTOK", "20")

    result = commentary.generate_commentary(
        ticker="AAPL",
        metrics={"mae_model": 1.0},
        last_close=100.0,
        forecast=[100.0, 101.0],
    )

    # 1000/1e6 * 10 + 2000/1e6 * 20 = 0.01 + 0.04
    assert result["cost_usd"] == 0.05
