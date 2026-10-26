"""The browser tests' NVDA response must stay what the API returns.

tests/web/fixtures/nvda_v3.json is served by the mock API of the browser
tests, which run without app/. These checks run in the main suite and
recompute what the service derives (the numbers check, the rendered text,
the commentary block), so the fixture cannot drift from the code.
"""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from app import commentary
from app.evals import check_numbers
from app.main import HISTORY_POINTS, ForecastResponse

ROOT = Path(__file__).resolve().parents[1]


def _load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


FIXTURE = _load("tests/web/fixtures/nvda_v3.json")
CASE = _load("evals/cases/en-nvda-30-beats.json")
RECORDED = _load("evals/recorded/2026-10-25.3/en-nvda-30-beats-1.json")


def _payload(body: dict) -> dict:
    """What generate_commentary hands the numbers check."""
    return {
        "ticker": body["ticker"],
        "last_close": body["last_close"],
        "forecast": body["forecast"],
        "metrics": body["metrics"],
        "horizon_days": len(body["forecast"]),
    }


def test_fixture_is_the_eval_case_and_its_recorded_v3_reply():
    assert FIXTURE["ticker"] == CASE["ticker"]
    assert FIXTURE["horizon_days"] == CASE["horizon"] == len(FIXTURE["forecast"])
    for key in ("last_close", "forecast", "metrics"):
        assert FIXTURE[key] == CASE[key]
    c = FIXTURE["commentary"]
    assert RECORDED["case_id"] == CASE["id"]
    for key in (
        "text",
        "model",
        "prompt_version",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "stop_reason",
        "generated_at",
    ):
        assert c[key] == RECORDED[key], key
    assert c["prompt_version"] == commentary.PROMPT_VERSION


def test_fixture_has_the_shape_of_the_api_response():
    model = ForecastResponse.model_validate(FIXTURE)
    assert model.model_dump(mode="json") == FIXTURE


def test_stored_eval_is_what_the_guard_computes():
    c = FIXTURE["commentary"]
    assert c["eval"] == check_numbers(c["text"], _payload(FIXTURE))


def test_stored_sections_render_to_the_stored_text():
    c = FIXTURE["commentary"]
    verdict = commentary.verdict_key(FIXTURE["metrics"])
    assert c["sections"]["verdict"] == commentary.VERDICTS["en"][verdict]
    assert c["sections"]["disclaimer"] == commentary.DISCLAIMERS["en"]
    assert commentary.render(c["sections"], "en", verdict) == c["text"]


def test_commentary_block_is_what_the_service_builds_from_the_reply(monkeypatch):
    """The recorded reply through call_model and generate_commentary."""
    c = FIXTURE["commentary"]
    reply = json.dumps({k: c["sections"][k] for k in commentary.SECTIONS})
    message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=reply)],
        stop_reason=c["stop_reason"],
        usage=SimpleNamespace(
            input_tokens=c["input_tokens"], output_tokens=c["output_tokens"]
        ),
    )
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: message))
    monkeypatch.setattr(commentary, "Anthropic", lambda *a, **kw: client)
    monkeypatch.setenv("COMMENTARY_CACHE_ENABLED", "false")
    monkeypatch.setenv("ANTHROPIC_MODEL", c["model"])
    monkeypatch.delenv("ANTHROPIC_PRICE_INPUT_PER_MTOK", raising=False)
    monkeypatch.delenv("ANTHROPIC_PRICE_OUTPUT_PER_MTOK", raising=False)

    block = commentary.generate_commentary(
        ticker=FIXTURE["ticker"],
        metrics=FIXTURE["metrics"],
        last_close=FIXTURE["last_close"],
        forecast=FIXTURE["forecast"],
        language="en",
    )
    assert block == {**c, "generated_at": block["generated_at"]}


def test_history_is_smooth_trading_days_ending_at_the_last_close():
    history = FIXTURE["history"]
    days = [date.fromisoformat(p["date"]) for p in history]
    closes = [p["close"] for p in history]
    assert len(history) == HISTORY_POINTS
    assert all(d.weekday() < 5 for d in days)
    assert days == sorted(set(days))
    assert days[-1] < date.fromisoformat(FIXTURE["commentary"]["generated_at"][:10])
    assert closes[-1] == FIXTURE["last_close"]
    # No jump anywhere, the last step included: every move is under 1 %.
    moves = [abs(b - a) / a for a, b in zip(closes, closes[1:])]
    assert max(moves) < 0.01
