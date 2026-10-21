"""Offline tests for app.commentary — no network, no API keys."""

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
