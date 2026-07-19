"""ForecastPilot API — time-series forecasts with optional AI commentary."""

import os

import yfinance as yf
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from app.commentary import generate_commentary
from app.forecasting import fit_forecast

load_dotenv()  # load ANTHROPIC_API_KEY (and friends) from the project-root .env

app = FastAPI(
    title="ForecastPilot",
    version="0.1.0",
    description=(
        "Time-series forecasting with AI-generated commentary. "
        "Educational project — not investment advice."
    ),
)


class ForecastResponse(BaseModel):
    ticker: str
    horizon_days: int
    last_close: float
    forecast: list[float]
    metrics: dict
    commentary: dict | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/forecast/{ticker}", response_model=ForecastResponse)
def forecast(
    ticker: str,
    horizon: int = Query(default=30, ge=5, le=90),
    commentary: bool = Query(
        default=False, description="Generate Claude commentary (requires API key)"
    ),
    language: str = Query(default="en", pattern="^(en|de)$"),
) -> ForecastResponse:
    series = _load_close_series(ticker)
    result = fit_forecast(series, horizon=horizon)

    commentary_block = None
    if commentary:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise HTTPException(
                status_code=503, detail="ANTHROPIC_API_KEY not configured"
            )
        commentary_block = generate_commentary(
            ticker=ticker,
            metrics=result["metrics"],
            last_close=float(series.iloc[-1]),
            forecast=result["forecast"],
            language=language,
        )

    return ForecastResponse(
        ticker=ticker.upper(),
        horizon_days=horizon,
        last_close=round(float(series.iloc[-1]), 2),
        forecast=[round(v, 2) for v in result["forecast"]],
        metrics=result["metrics"],
        commentary=commentary_block,
    )


def _load_close_series(ticker: str):
    """Fetch ~2y of daily closes.

    Kept separate from forecasting so the model stays unit-testable offline.
    """
    data = yf.download(
        ticker, period="2y", interval="1d", progress=False, auto_adjust=True
    )
    if data is None or data.empty:
        raise HTTPException(
            status_code=404, detail=f"No data found for ticker '{ticker}'"
        )
    close = data["Close"].dropna()
    if hasattr(close, "squeeze"):  # yfinance may return a 1-column DataFrame
        close = close.squeeze()
    if len(close) < 60:
        raise HTTPException(status_code=422, detail="Not enough history to forecast")
    return close
