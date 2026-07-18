"""LLM commentary layer — Claude via the Anthropic API, with token tracking."""

import os

from anthropic import Anthropic

SYSTEM = (
    "You are a markets analyst writing short, factual commentary for a management "
    "audience. Use ONLY the numbers provided. Never invent figures. 120 words max. "
    "End with: 'Educational analysis — not investment advice.'"
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
        max_tokens=400,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    return {
        "text": msg.content[0].text,
        "model": model,
        "input_tokens": msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
        # TODO(W5): cost estimate from configurable per-token rates
        # TODO(W5): numbers-match eval guard — verify every figure in `text`
        #           appears in the computed inputs before returning it
    }
