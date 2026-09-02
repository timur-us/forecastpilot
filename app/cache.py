"""Daily in-memory cache for generated commentary.

Plain dict + timestamps, no external deps, no Redis. Cache is per-process:
with scale-to-zero and a single replica this is intentional and sufficient
— a cold start just means the first request of the day recomputes.
"""

import os
import time
from datetime import datetime, timedelta, timezone

CacheKey = tuple[str, str, int]

_store: dict[CacheKey, tuple[dict, float]] = {}


def _enabled() -> bool:
    return os.getenv("COMMENTARY_CACHE_ENABLED", "true").strip().lower() not in {
        "false",
        "0",
        "no",
        "off",
    }


def _make_key(ticker: str, language: str, horizon_days: int) -> CacheKey:
    return (ticker.upper(), language, horizon_days)


def _seconds_until_next_utc_midnight() -> float:
    now = datetime.now(timezone.utc)
    next_midnight = datetime.combine(
        now.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
    )
    return (next_midnight - now).total_seconds()


def get(ticker: str, language: str, horizon_days: int) -> dict | None:
    """Return a cached commentary block, or None on a miss/expiry/disable."""
    if not _enabled():
        return None
    key = _make_key(ticker, language, horizon_days)
    entry = _store.get(key)
    if entry is None:
        return None
    value, expires_at = entry
    if time.time() >= expires_at:
        del _store[key]
        return None
    return dict(value)


def set(ticker: str, language: str, horizon_days: int, value: dict) -> None:
    """Cache a commentary block until the next UTC midnight (max ~24h)."""
    if not _enabled():
        return
    key = _make_key(ticker, language, horizon_days)
    _store[key] = (dict(value), time.time() + _seconds_until_next_utc_midnight())
