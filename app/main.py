"""ForecastPilot API — time-series forecasts with optional AI commentary."""

import os
from datetime import date
from urllib.parse import urlsplit

import anthropic
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.extension import HEADERS

from app.clientip import client_ip
from app.commentary import CommentaryError, describe_llm_error, generate_commentary
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
# client_ip resolves the caller behind a trusted reverse proxy (app.clientip).
# headers_enabled writes X-RateLimit-Limit/-Remaining/-Reset (Reset is a Unix
# timestamp) and Retry-After on every limited response.


class _HeadersOnceLimiter(Limiter):
    """A Limiter whose header injection is idempotent.

    The forecast endpoint stacks two @limit decorators (plain forecast,
    commentary); slowapi evaluates both limits in one pass but each
    decorator's wrapper injects the headers, which would append every
    X-RateLimit-* header twice ("30, 30"). The first injection wins.
    """

    def _inject_headers(self, response, current_limit):
        if (
            isinstance(response, Response)
            and self._header_mapping[HEADERS.LIMIT] in response.headers
        ):
            return response
        return super()._inject_headers(response, current_limit)


limiter = _HeadersOnceLimiter(
    key_func=client_ip, key_style="endpoint", headers_enabled=True
)
app.state.limiter = limiter

RATE_LIMIT_HEADERS = [
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
    "Retry-After",
]


def _cors_origins() -> list[str]:
    """Browser origins allowed to call the API (CORS_ORIGINS, comma-separated).

    An origin is scheme + host (+ port); trailing slashes are dropped. Values
    with a path, without a scheme, or the wildcard "*" are configuration
    errors and refused at startup rather than silently never matching.
    """
    origins = []
    for raw in os.getenv("CORS_ORIGINS", "").split(","):
        origin = raw.strip().rstrip("/")
        if not origin:
            continue
        parts = urlsplit(origin)
        if origin == "*" or parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError(f"CORS_ORIGINS: {raw.strip()!r} is not an origin")
        if parts.path or parts.query or parts.fragment:
            raise ValueError(f"CORS_ORIGINS: {raw.strip()!r} must not contain a path")
        origins.append(origin)
    return origins


# CORS is off unless origins are configured; the deploy job sets the GitHub
# Pages origin that hosts the web UI. The rate-limit headers are exposed so
# the page can show the remaining budget.
if _cors_origins():
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_methods=["GET"],
        expose_headers=RATE_LIMIT_HEADERS,
    )


def _handle_rate_limit_exceeded(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    response = JSONResponse(
        status_code=429, content={"detail": f"Rate limit exceeded: {exc.detail}"}
    )
    # The limiter that evaluated this request, as slowapi's own handler does.
    return request.app.state.limiter._inject_headers(
        response, request.state.view_rate_limit
    )


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


class ClosePoint(BaseModel):
    date: date
    close: float


class ForecastResponse(BaseModel):
    ticker: str
    horizon_days: int
    last_close: float
    forecast: list[float]
    metrics: dict
    history: list[ClosePoint] = []
    commentary: dict | None = None


HISTORY_POINTS = 60


def _history(series) -> list[ClosePoint]:
    """The last HISTORY_POINTS closes with their dates, oldest first."""
    return [
        ClosePoint(date=pd.Timestamp(day).date(), close=round(float(v), 2))
        for day, v in series.tail(HISTORY_POINTS).items()
    ]


WEB_UI_URL = os.getenv("WEB_UI_URL", "https://timur-us.github.io/forecastpilot/")


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def root() -> RedirectResponse:
    """The web UI lives on GitHub Pages; /docs stays the API reference."""
    return RedirectResponse(url=WEB_UI_URL)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/forecast/{ticker}", response_model=ForecastResponse)
@limiter.limit(
    _commentary_rate_limit, exempt_when=lambda request: not _wants_commentary(request)
)
@limiter.limit(_forecast_rate_limit, exempt_when=_wants_commentary)
def forecast(
    request: Request,
    response: Response,  # slowapi writes the rate-limit headers onto it
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
        except (anthropic.APIError, CommentaryError) as e:
            raise HTTPException(status_code=502, detail=describe_llm_error(e)) from e

    return ForecastResponse(
        ticker=ticker.upper(),
        horizon_days=horizon,
        last_close=round(float(series.iloc[-1]), 2),
        forecast=[round(v, 2) for v in result["forecast"]],
        metrics=result["metrics"],
        history=_history(series),
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
