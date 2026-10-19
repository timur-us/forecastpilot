"""Offline tests for app.commentary — no network, no API keys."""

from types import SimpleNamespace

import pytest

from app import commentary


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _thinking(thinking="..."):
    return SimpleNamespace(type="thinking", thinking=thinking)


class _FakeMessages:
    def __init__(self, content):
        self.content = content

    def create(self, **kwargs):
        return SimpleNamespace(
            content=self.content,
            usage=SimpleNamespace(input_tokens=1000, output_tokens=2000),
        )


class _FakeClient:
    def __init__(self, content):
        self.messages = _FakeMessages(content)


def _install_client(monkeypatch, content=None):
    """Install a fake Anthropic client that answers with the given blocks."""
    if content is None:
        content = [_text("Fake commentary. Last close 100.00.")]
    client = _FakeClient(content)
    monkeypatch.setattr(commentary, "Anthropic", lambda *a, **kw: client)
    monkeypatch.setenv("COMMENTARY_CACHE_ENABLED", "false")


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

    # 1000/1e6 * 3.0 + 2000/1e6 * 15.0 = 0.003 + 0.03
    assert result["cost_usd"] == 0.033


def test_cost_usd_respects_env_pricing(monkeypatch):
    _install_client(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_PRICE_INPUT_PER_MTOK", "10")
    monkeypatch.setenv("ANTHROPIC_PRICE_OUTPUT_PER_MTOK", "20")

    result = _call()

    # 1000/1e6 * 10 + 2000/1e6 * 20 = 0.01 + 0.04
    assert result["cost_usd"] == 0.05


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
