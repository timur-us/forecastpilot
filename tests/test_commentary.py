"""Offline tests for app.commentary — no network, no API keys."""

import re
from types import SimpleNamespace

import pytest

from app import commentary


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _thinking(thinking="..."):
    return SimpleNamespace(type="thinking", thinking=thinking)


class _FakeMessages:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            content=self.content,
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=1000, output_tokens=2000),
        )


class _FakeClient:
    def __init__(self, content, stop_reason):
        self.messages = _FakeMessages(content, stop_reason)


def _install_client(monkeypatch, content=None, stop_reason="end_turn"):
    """Install a fake Anthropic client that answers with the given blocks."""
    if content is None:
        content = [_text("Fake commentary. Last close 100.00.")]
    client = _FakeClient(content, stop_reason)
    monkeypatch.setattr(commentary, "Anthropic", lambda *a, **kw: client)
    monkeypatch.setenv("COMMENTARY_CACHE_ENABLED", "false")
    return client


def _call():
    return commentary.generate_commentary(
        ticker="AAPL",
        metrics={"mae_model": 1.0},
        last_close=100.0,
        forecast=[100.0, 101.0],
    )


def test_cost_usd_uses_default_pricing(monkeypatch):
    _install_client(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_PRICE_INPUT_PER_MTOK", raising=False)
    monkeypatch.delenv("ANTHROPIC_PRICE_OUTPUT_PER_MTOK", raising=False)

    result = _call()

    # 1000/1e6 * 2.0 + 2000/1e6 * 10.0 = 0.002 + 0.02
    assert result["cost_usd"] == 0.022


def test_default_model_is_claude_sonnet_5_5(monkeypatch):
    _install_client(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)

    result = _call()

    assert result["model"] == "claude-sonnet-5-5"


def test_cost_usd_respects_env_pricing(monkeypatch):
    _install_client(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_PRICE_INPUT_PER_MTOK", "10")
    monkeypatch.setenv("ANTHROPIC_PRICE_OUTPUT_PER_MTOK", "20")

    result = _call()

    # 1000/1e6 * 10 + 2000/1e6 * 20 = 0.01 + 0.04
    assert result["cost_usd"] == 0.05


def test_request_uses_the_token_budget_with_room_for_thinking(monkeypatch):
    client = _install_client(monkeypatch)

    _call()

    assert client.messages.last_kwargs["max_tokens"] == commentary.MAX_TOKENS
    # Floor: ~450 text tokens (German) plus the ~300 thinking tokens observed
    # at the former 600 limit, with headroom.
    assert commentary.MAX_TOKENS >= 1024


def test_complete_response_reports_stop_reason_and_is_not_truncated(monkeypatch):
    _install_client(monkeypatch)

    result = _call()

    assert result["stop_reason"] == "end_turn"
    assert result["truncated"] is False


def test_response_cut_at_the_budget_is_flagged_as_truncated(monkeypatch):
    _install_client(
        monkeypatch, [_text("Last close 100.00. The forecast")], "max_tokens"
    )

    result = _call()

    assert result["truncated"] is True
    assert result["stop_reason"] == "max_tokens"
    assert result["eval"]["passed"] is True  # the numbers check stays a separate signal


def test_refused_response_raises_instead_of_returning_partial_text(monkeypatch):
    _install_client(monkeypatch, [_text("Partial")], "refusal")

    with pytest.raises(commentary.CommentaryError, match="refusal"):
        _call()


def test_text_is_assembled_from_text_blocks_after_thinking(monkeypatch):
    _install_client(monkeypatch, [_thinking(), _text("Visible "), _text("commentary.")])

    result = _call()

    assert result["text"] == "Visible commentary."


def test_response_without_text_block_raises_commentary_error(monkeypatch):
    _install_client(monkeypatch, [_thinking()])

    with pytest.raises(commentary.CommentaryError, match="empty response"):
        _call()


def test_describe_llm_error_maps_commentary_error():
    detail = commentary.describe_llm_error(commentary.CommentaryError("empty response"))

    assert detail == "LLM call failed: empty response"


def test_commentary_block_carries_the_prompt_version(monkeypatch):
    _install_client(monkeypatch)

    result = _call()

    assert result["prompt_version"] == commentary.PROMPT_VERSION
    # A date plus a counter, e.g. "2026-10-23.1".
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", commentary.PROMPT_VERSION)


def test_call_model_returns_the_raw_reply_and_sends_the_prompt(monkeypatch):
    client = _install_client(monkeypatch, [_thinking(), _text("Raw text.")])
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    payload = {
        "ticker": "sap.de",
        "last_close": 123.456,
        "forecast": [120.0, 121.5, 125.0],
        "metrics": {"mae_model": 6.8767},
        "horizon_days": 3,
    }

    reply = commentary.call_model(payload, "de")

    assert reply == commentary.ModelReply(
        text="Raw text.",
        stop_reason="end_turn",
        model="claude-sonnet-5-5",
        input_tokens=1000,
        output_tokens=2000,
    )
    sent = client.messages.last_kwargs
    assert sent["system"] == commentary.SYSTEM
    assert sent["max_tokens"] == commentary.MAX_TOKENS
    assert sent["messages"] == [
        {"role": "user", "content": commentary.user_prompt(payload, "de")}
    ]


def test_user_prompt_names_the_language_and_prints_two_decimals():
    payload = {
        "ticker": "sap.de",
        "last_close": 123.456,
        "forecast": [120.0, 121.5, 125.0],
        "metrics": {"mae_model": 6.8767, "beats_naive": False},
        "horizon_days": 3,
    }

    prompt = commentary.user_prompt(payload, "de")

    assert prompt.splitlines() == [
        "Write the commentary in German.",
        "Ticker: SAP.DE",
        "Last close: 123.46",
        "Forecast start: 120.00 | Forecast end: 125.00 (3 trading days)",
        "Backtest metrics: {'mae_model': 6.8767, 'beats_naive': False}",
    ]
    assert commentary.user_prompt(payload, "en").startswith(
        "Write the commentary in English."
    )


def test_call_model_raises_on_refusal_and_on_missing_text(monkeypatch):
    payload = {
        "ticker": "AAPL",
        "last_close": 100.0,
        "forecast": [100.0, 101.0],
        "metrics": {},
        "horizon_days": 2,
    }
    _install_client(monkeypatch, [_text("Partial")], "refusal")
    with pytest.raises(commentary.CommentaryError, match="refusal"):
        commentary.call_model(payload, "en")

    _install_client(monkeypatch, [_thinking()], "end_turn")
    with pytest.raises(commentary.CommentaryError, match="stop_reason=end_turn"):
        commentary.call_model(payload, "en")


def test_cost_usd_helper_applies_env_prices_and_rounds_to_six_decimals(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_PRICE_INPUT_PER_MTOK", "3")
    monkeypatch.setenv("ANTHROPIC_PRICE_OUTPUT_PER_MTOK", "15")

    # 461/1e6 * 3 + 265/1e6 * 15 = 0.001383 + 0.003975
    assert commentary.cost_usd(461, 265) == 0.005358
    # 1/1e6 * 3 + 7/1e6 * 15 is 0.00010800000000000001 in float arithmetic;
    # the rounding makes it the printable 0.000108.
    assert commentary.cost_usd(1, 7) == 0.000108
