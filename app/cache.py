"""Daily in-memory cache for generated commentary.

Entries are keyed by ticker, language, horizon and a fingerprint of the
numbers the commentary cites (last close, forecast start and end), so cached
text is reused only while those numbers are unchanged — a price move during
the trading day yields fresh commentary instead of text about older prices.

Plain dict + timestamps, no external deps, no Redis. Cache is per-process:
with scale-to-zero and a single replica this is intentional and sufficient
— a cold start just means the first request of the day recomputes.
"""

import os
import time
from datetime import datetime, timedelta, timezone

Fingerprint = tuple[str, ...]
CacheKey = tuple[str, str, int, Fingerprint]

_store: dict[CacheKey, tuple[dict, float]] = {}


def _enabled() -> bool:
    return os.getenv("COMMENTARY_CACHE_ENABLED", "true").strip().lower() not in {
        "false",
        "0",
        "no",
        "off",
    }


def _make_key(
    ticker: str, language: str, horizon_days: int, fingerprint: Fingerprint
) -> CacheKey:
    return (ticker.upper(), language, horizon_days, tuple(fingerprint))


def _seconds_until_next_utc_midnight() -> float:
    now = datetime.now(timezone.utc)
    next_midnight = datetime.combine(
        now.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
    )
    return (next_midnight - now).total_seconds()


def get(
    ticker: str, language: str, horizon_days: int, fingerprint: Fingerprint
) -> dict | None:
    """Return a cached commentary block, or None on a miss/expiry/disable."""
    if not _enabled():
        return None
    key = _make_key(ticker, language, horizon_days, fingerprint)
    entry = _store.get(key)
    if entry is None:
        return None
    value, expires_at = entry
    if time.time() >= expires_at:
        del _store[key]
        return None
    return dict(value)


def set(
    ticker: str, language: str, horizon_days: int, fingerprint: Fingerprint, value: dict
) -> None:
    """Cache a commentary block until the next UTC midnight (max ~24h).

    Expired entries are dropped on the way: with the numbers in the key, an
    old entry is rarely looked up again, so eviction cannot rely on get().
    """
    if not _enabled():
        return
    now = time.time()
    for stale in [k for k, (_, expires_at) in _store.items() if now >= expires_at]:
        del _store[stale]
    key = _make_key(ticker, language, horizon_days, fingerprint)
    _store[key] = (dict(value), now + _seconds_until_next_utc_midnight())
