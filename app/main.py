"""ForecastPilot API — time-series forecasts with optional AI commentary."""

import os

import anthropic
import yfinance as yf
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.commentary import describe_llm_error, generate_commentary
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

# key_style="endpoint" pools requests by (client, route) regardless of the
# ticker in the URL — the default "url" style would give every ticker its
# own bucket, making the limit trivially bypassable by varying the ticker.
limiter = Limiter(key_func=get_remote_address, key_style="endpoint")
app.state.limiter = limiter


def _handle_rate_limit_exceeded(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    response = JSONResponse(
        status_code=429, content={"detail": f"Rate limit exceeded: {exc.detail}"}
    )
    return limiter._inject_headers(response, request.state.view_rate_limit)


app.add_exception_handler(RateLimitExceeded, _handle_rate_limit_exceeded)


def _forecast_rate_limit() -> str:
    return os.getenv("RATE_LIMIT_FORECAST", "30/hour")


def _commentary_rate_limit() -> str:
    return os.getenv("RATE_LIMIT_COMMENTARY", "5/hour")


def _wants_commentary(request: Request) -> bool:
    """Read the raw `commentary` query param.

    slowapi's exempt_when only gets the raw Request (not the endpoint's
    parsed keyword arguments), so this re-parses the same truthy values
    FastAPI/pydantic accept for a bool query param.
    """
    raw = request.query_params.get("commentary", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class ForecastResponse(BaseModel):
    ticker: str
    horizon_days: int
    last_close: float
    forecast: list[float]
    metrics: dict
    commentary: dict | None = None


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/forecast/{ticker}", response_model=ForecastResponse)
@limiter.limit(_commentary_rate_limit, exempt_when=lambda request: not _wants_commentary(request))
@limiter.limit(_forecast_rate_limit, exempt_when=_wants_commentary)
def forecast(
    request: Request,
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
        try:
            commentary_block = generate_commentary(
                ticker=ticker,
                metrics=result["metrics"],
                last_close=float(series.iloc[-1]),
                forecast=result["forecast"],
                language=language,
            )
        except anthropic.APIError as e:
            raise HTTPException(status_code=502, detail=describe_llm_error(e)) from e

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
