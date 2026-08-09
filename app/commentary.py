"""LLM commentary layer — Claude via the Anthropic API, with token tracking."""

import os

import anthropic
from anthropic import Anthropic

from app.evals import check_numbers

# Maps Anthropic SDK exception types to a short, user-facing reason string
# used to build the "LLM call failed: <reason>" HTTP 502 detail in app.main.
LLM_ERROR_REASONS: dict[type[Exception], str] = {
    anthropic.AuthenticationError: "authentication",
    anthropic.PermissionDeniedError: "insufficient credits or permissions",
    anthropic.RateLimitError: "rate limit",
    anthropic.APIConnectionError: "network",
}


def describe_llm_error(exc: anthropic.APIError) -> str:
    """Turn an Anthropic SDK exception into a short 'LLM call failed: ...' reason."""
    for exc_type, reason in LLM_ERROR_REASONS.items():
        if isinstance(exc, exc_type):
            return f"LLM call failed: {reason}"
    if isinstance(exc, anthropic.APIStatusError):
        return f"LLM call failed: upstream error ({exc.status_code})"
    return "LLM call failed: unexpected error"


SYSTEM = (
    "You are a markets analyst writing short, factual commentary for a management "
    "audience. Write in exactly three parts, in this fixed order:\n"
    "1. Kursbewegung / Price movement — last close vs. the forecast start and end.\n"
    "2. Modellgüte / Model quality — backtest accuracy, explicitly stating whether "
    "the model beats the naive baseline (beats_naive). If beats_naive is not present "
    "in the data, say the backtest could not be computed — do not guess.\n"
    "3. Einschränkung / Interpretation — what the forecast does and does not imply.\n"
    "Hard rule: use ONLY numbers that appear in the data given to you. Never invent, "
    "estimate, or round to a figure that isn't given.\n"
    "Maximum 150 words total. Write the entire commentary — including section "
    "labels — in the requested language; do not mix languages.\n"
    "End with the disclaimer in the requested language: "
    "'Educational analysis — not investment advice.' in English, or "
    "'Bildungsanalyse — keine Anlageberatung.' in German."
)


def generate_commentary(
    ticker: str,
    metrics: dict,
    last_close: float,
    forecast: list,
    language: str = "en",
) -> dict:
    """Generate a short management commentary grounded in the computed numbers."""
    client = Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    price_input = float(os.getenv("ANTHROPIC_PRICE_INPUT_PER_MTOK", "3.0"))
    price_output = float(os.getenv("ANTHROPIC_PRICE_OUTPUT_PER_MTOK", "15.0"))

    lang = "English" if language == "en" else "German"
    prompt = (
        f"Write the commentary in {lang}.\n"
        f"Ticker: {ticker.upper()}\n"
        f"Last close: {last_close:.2f}\n"
        f"Forecast start: {forecast[0]:.2f} | Forecast end: {forecast[-1]:.2f} "
        f"({len(forecast)} trading days)\n"
        f"Backtest metrics: {metrics}"
    )

    msg = client.messages.create(
        model=model,
        max_tokens=600,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    cost_usd = round(
        msg.usage.input_tokens / 1e6 * price_input
        + msg.usage.output_tokens / 1e6 * price_output,
        6,
    )
    text = msg.content[0].text
    payload = {
        "last_close": last_close,
        "forecast": forecast,
        "metrics": metrics,
        "horizon_days": len(forecast),
    }

    return {
        "text": text,
        "model": model,
        "input_tokens": msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
        "cost_usd": cost_usd,
        "eval": check_numbers(text, payload),
    }
