"""LLM commentary layer — Claude via the Anthropic API, with token tracking."""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import anthropic
from anthropic import Anthropic

from app import cache
from app.evals import check_numbers


class CommentaryError(RuntimeError):
    """The model response could not be turned into commentary text."""


# Maps Anthropic SDK exception types to a short, user-facing reason string
# used to build the "LLM call failed: <reason>" HTTP 502 detail in app.main.
LLM_ERROR_REASONS: dict[type[Exception], str] = {
    anthropic.AuthenticationError: "authentication",
    anthropic.PermissionDeniedError: "insufficient credits or permissions",
    anthropic.RateLimitError: "rate limit",
    anthropic.APIConnectionError: "network",
}


def describe_llm_error(exc: Exception) -> str:
    """Map an SDK or commentary exception to a short 'LLM call failed: ...' reason."""
    if isinstance(exc, CommentaryError):
        return f"LLM call failed: {exc}"
    for exc_type, reason in LLM_ERROR_REASONS.items():
        if isinstance(exc, exc_type):
            return f"LLM call failed: {reason}"
    if isinstance(exc, anthropic.APIStatusError):
        return f"LLM call failed: upstream error ({exc.status_code})"
    return "LLM call failed: unexpected error"


# Labels the prompt that produced a commentary block: SYSTEM plus the user
# turn built by user_prompt. A date plus a counter; bump it with every edit
# to either text so recorded evaluation results stay attributable to one
# prompt. 2026-10-23.1 was the first label (text unchanged since 2026-08-09,
# d3e40aa); 2026-10-25.1 added rules (labels, verdict sentences, number
# format, no code, no title line) and was rejected: English replies computed
# the change against the last close; 2026-10-25.2 added one rule for part 1
# (name the three figures, compute nothing). 2026-10-25.3 switches the model
# step to structured outputs: the model returns the prose of the three parts
# as JSON fields, the code renders labels, verdict sentence and disclaimer.
PROMPT_VERSION = "2026-10-25.3"

# What the code renders around the model's prose, per language — the exact
# wording prompt 2026-10-25.2 prescribed, now guaranteed by construction.
LABELS = {
    "en": ("Price movement", "Model quality", "Interpretation"),
    "de": ("Kursbewegung", "Modellgüte", "Einschränkung"),
}
VERDICTS = {
    "en": {
        "beats": "The model beats the naive baseline.",
        "does_not_beat": "The model does not beat the naive baseline.",
        "no_backtest": "No backtest was possible.",
    },
    "de": {
        "beats": "Das Modell schlägt die naive Baseline.",
        "does_not_beat": "Das Modell schlägt die naive Baseline nicht.",
        "no_backtest": "Ein Backtest war nicht möglich.",
    },
}
DISCLAIMERS = {
    "en": "Educational analysis — not investment advice.",
    "de": "Bildungsanalyse — keine Anlageberatung.",
}
SECTIONS = ("price_movement", "model_quality", "interpretation")

# The model delivers only what needs a model: the prose of the three parts.
# Structured outputs (output_config.format, platform.claude.com/docs/en/
# build-with-claude/structured-outputs) constrain the reply to this schema;
# the grammar does not touch thinking, which stays on.
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "price_movement": {
            "type": "string",
            "description": "Part 1: the last close, the forecast start and the forecast end.",
        },
        "model_quality": {
            "type": "string",
            "description": "Part 2: the backtest accuracy, without the verdict sentence.",
        },
        "interpretation": {
            "type": "string",
            "description": "Part 3: what the forecast does and does not imply.",
        },
    },
    "required": list(SECTIONS),
    "additionalProperties": False,
}

SYSTEM = (
    "You are a markets analyst writing short, factual commentary for a management "
    "audience.\n"
    "Return the three parts of the commentary as the fields price_movement, "
    "model_quality and interpretation. The labels, the verdict on the naive "
    "baseline and the disclaimer are added by the system: do not include them.\n"
    "price_movement: name the last close, the forecast start and the forecast "
    "end. Do not compute differences, changes or percentages; cite only figures "
    "given in the data.\n"
    "model_quality: the backtest accuracy from the given metrics. If the data "
    "has no beats_naive there was no backtest: cite no error figures and do not "
    "guess.\n"
    "interpretation: what the forecast does and does not imply.\n"
    "Hard rule: use ONLY numbers that appear in the data given to you. Never "
    "invent, estimate, or round to a figure that isn't given. Write numbers in "
    "the conventions of the requested language: German with a decimal comma and "
    "a point as thousands separator (18.234,56; 6,88), English with a decimal "
    "point and a comma as thousands separator (18,234.56; 6.88).\n"
    "Write every field in the requested language; do not mix languages. Write "
    "plain prose for people: do not quote field names or code from the data (no "
    "beats_naive, no True or False, no mae_model); say MAE, MAPE and backtest in "
    "words.\n"
    "Maximum 130 words across the three fields."
)


# Hard limit on thinking plus reply. Claude Sonnet 5.5 thinks adaptively and
# those tokens count toward max_tokens even though they are not returned
# (platform.claude.com/docs/en/build-with-claude/thinking), so the budget
# leaves ample room beyond the ~150-word commentary (German runs to ~450
# tokens). Only generated tokens are billed, not the limit.
MAX_TOKENS = 2048


@dataclass(frozen=True)
class ModelReply:
    """What one model call returned, before cache, cost and eval are added.

    This is the seam for replacing the model step (for example with structured
    outputs): only call_model changes, everything around it keeps consuming
    this reply.
    """

    text: str
    stop_reason: str | None
    model: str
    input_tokens: int
    output_tokens: int
    sections: dict | None = (
        None  # the model's prose plus the rendered verdict and disclaimer
    )


def _fingerprint(last_close: float, forecast: list) -> tuple[str, str, str]:
    """The numbers the prompt cites verbatim, formatted as the prompt does."""
    return (f"{last_close:.2f}", f"{forecast[0]:.2f}", f"{forecast[-1]:.2f}")


def user_prompt(payload: dict, language: str) -> str:
    """The user turn: language, ticker, the cited numbers (two decimals), metrics."""
    lang = "English" if language == "en" else "German"
    forecast = payload["forecast"]
    return (
        f"Write the commentary in {lang}.\n"
        f"Ticker: {payload['ticker'].upper()}\n"
        f"Last close: {payload['last_close']:.2f}\n"
        f"Forecast start: {forecast[0]:.2f} | Forecast end: {forecast[-1]:.2f} "
        f"({len(forecast)} trading days)\n"
        f"Backtest metrics: {payload['metrics']}"
    )


def verdict_key(metrics: dict) -> str:
    """Which verdict sentence the data supports."""
    if "beats_naive" not in metrics:
        return "no_backtest"
    return "beats" if metrics["beats_naive"] else "does_not_beat"


def render(sections: dict, language: str, verdict: str) -> str:
    """The commentary text: labels, the model's prose, the verdict, the disclaimer."""
    labels = LABELS[language]
    return "\n\n".join(
        [
            f"**{labels[0]}**\n{sections['price_movement']}",
            f"**{labels[1]}**\n{sections['model_quality']} {VERDICTS[language][verdict]}",
            f"**{labels[2]}**\n{sections['interpretation']}",
            DISCLAIMERS[language],
        ]
    )


def _parse_sections(text: str) -> dict:
    """The three prose fields of a structured reply, or CommentaryError."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CommentaryError(f"invalid structured reply: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise CommentaryError("invalid structured reply: not an object")
    sections = {}
    for key in SECTIONS:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise CommentaryError(f"invalid structured reply: missing {key}")
        sections[key] = value.strip()
    return sections


def call_model(payload: dict, language: str) -> ModelReply:
    """One model call: build the prompt, send it, render the structured reply.

    `payload` carries ticker, last_close, forecast, metrics and horizon_days —
    the same dict the numbers check reads. The reply is constrained to
    OUTPUT_SCHEMA; the text of the ModelReply is the rendered commentary.
    Raises CommentaryError when the reply is a refusal, is cut off at
    MAX_TOKENS, holds no text, or is not valid JSON with the three fields —
    there is no partial commentary any more.
    """
    client = Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")

    msg = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        messages=[{"role": "user", "content": user_prompt(payload, language)}],
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    )

    # Responses may interleave thinking blocks with text; the JSON is the
    # concatenation of all text blocks.
    text = "".join(
        block.text for block in msg.content if getattr(block, "type", None) == "text"
    ).strip()
    if msg.stop_reason == "refusal":
        # Partial output of a refused request is incomplete by definition.
        raise CommentaryError("refusal")
    if msg.stop_reason == "max_tokens":
        raise CommentaryError("incomplete structured reply (stop_reason=max_tokens)")
    if not text:
        raise CommentaryError(f"empty response (stop_reason={msg.stop_reason})")
    sections = _parse_sections(text)
    verdict = verdict_key(payload["metrics"])
    return ModelReply(
        text=render(sections, language, verdict),
        stop_reason=msg.stop_reason,
        model=model,
        input_tokens=msg.usage.input_tokens,
        output_tokens=msg.usage.output_tokens,
        sections={
            **sections,
            "verdict": VERDICTS[language][verdict],
            "disclaimer": DISCLAIMERS[language],
        },
    )


def cost_usd(input_tokens: int, output_tokens: int) -> float:
    """Estimated cost of one call from the configured per-million-token prices."""
    price_input = float(os.getenv("ANTHROPIC_PRICE_INPUT_PER_MTOK", "2.0"))
    price_output = float(os.getenv("ANTHROPIC_PRICE_OUTPUT_PER_MTOK", "10.0"))
    return round(
        input_tokens / 1e6 * price_input + output_tokens / 1e6 * price_output, 6
    )


def generate_commentary(
    ticker: str,
    metrics: dict,
    last_close: float,
    forecast: list,
    language: str = "en",
) -> dict:
    """Generate a short management commentary grounded in the computed numbers.

    Cached for the rest of the UTC day per (ticker, language, horizon) and
    the numbers the text cites — see app.cache. On a cache hit the numbers
    check runs against the current payload, so `eval` always describes the
    response it is part of. The key includes PROMPT_VERSION, so a block
    generated under an earlier prompt is never served under a later one.
    """
    horizon_days = len(forecast)
    payload = {
        "ticker": ticker,
        "last_close": last_close,
        "forecast": forecast,
        "metrics": metrics,
        "horizon_days": horizon_days,
    }
    fingerprint = _fingerprint(last_close, forecast)
    cached = cache.get(ticker, language, horizon_days, fingerprint, PROMPT_VERSION)
    if cached is not None:
        # The cited numbers are unchanged; the backtest metrics may differ
        # slightly between fits, so judge the text against this payload.
        return {
            **cached,
            "eval": check_numbers(cached["text"], payload),
            "cached": True,
        }

    reply = call_model(payload, language)

    # A reply cut off at the token budget raises in call_model (502, never
    # cached): with structured outputs there is no partial text to return.
    # `truncated` stays in the block for compatibility and is always false.
    result = {
        "text": reply.text,
        "sections": reply.sections,
        "model": reply.model,
        "prompt_version": PROMPT_VERSION,
        "input_tokens": reply.input_tokens,
        "output_tokens": reply.output_tokens,
        "cost_usd": cost_usd(reply.input_tokens, reply.output_tokens),
        "eval": check_numbers(reply.text, payload),
        "stop_reason": reply.stop_reason,
        "truncated": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if reply.stop_reason == "end_turn":
        cache.set(ticker, language, horizon_days, fingerprint, PROMPT_VERSION, result)
    return {**result, "cached": False}
