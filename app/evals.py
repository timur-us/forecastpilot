"""Numbers-match eval guard.

Checks that every number appearing in generated commentary can be traced
back to a number that was actually in the payload used to produce it (the
forecast, its metrics, or values derived from them). This is an
observability signal, not a gate: a failed check does not block the
response — see app.commentary.generate_commentary.
"""

import re

# A number token: either a group-separated number (thousands separators in
# either convention, e.g. "1.234,56" or "1,234.56"), or a plain integer/
# decimal (e.g. "182.34", "30", "150,25", "18,6936"). Currency signs ($, €,
# USD, EUR) and "%" are deliberately left outside the match — they don't
# change the numeric value. A negative lookbehind for letters keeps things
# like "Q4" from being read as the number 4.
#
# The "(?!\d)" after \d{3} in the thousands branch matters: a group only
# counts as a 3-digit thousands group when it isn't followed by more digits.
# Without it, a 4+-digit decimal fraction like "18,6936" would greedily
# match just its first 3 digits as a "thousands group" and strand the rest
# ("6") as a separate bogus token.
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z])[+-]?\d{1,3}(?:[.,]\d{3}(?!\d))+(?:[.,]\d+)?"
    r"|(?<![A-Za-z])[+-]?\d+(?:[.,]\d+)?"
)

# app.commentary.SYSTEM enforces a fixed "1./2./3." section structure;
# strip those list markers so they aren't mistaken for invented figures.
_LIST_MARKER_RE = re.compile(r"(?m)^\s*[123]\.\s+")

_TOLERANCE = 0.01  # absolute; accounts for rounding as printed


def _parse_number(token: str) -> float | None:
    """Normalize a DE- or EN-formatted number token to a float."""
    sign = ""
    if token and token[0] in "+-":
        sign, token = token[0], token[1:]

    if "," in token and "." in token:
        # Whichever separator comes last is the decimal point.
        decimal_sep = token[max(token.rfind(","), token.rfind("."))]
        thousands_sep = "." if decimal_sep == "," else ","
        token = token.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif "," in token:
        integer_part, _, frac = token.partition(",")
        if token.count(",") > 1 or (len(frac) == 3 and len(integer_part) <= 3):
            token = token.replace(",", "")  # thousands grouping, e.g. "1,234"
        else:
            token = token.replace(",", ".")  # decimal comma, e.g. "150,25"
    elif token.count(".") > 1:
        token = token.replace(".", "")  # thousands-only, e.g. "1.234.567"
    # else: plain integer, or a single "." already valid as a decimal point

    try:
        return float(sign + token)
    except ValueError:
        return None


def _extract_numbers(text: str) -> list[tuple[str, float]]:
    text = _LIST_MARKER_RE.sub("", text)
    found = []
    for match in _NUMBER_RE.finditer(text):
        value = _parse_number(match.group())
        if value is not None:
            found.append((match.group(), value))
    return found


def _allowed_numbers(payload: dict) -> set[float]:
    """Every number the commentary is allowed to cite, plus rounded variants."""
    raw: set[float] = set()

    def add(value: object) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            raw.add(float(value))

    add(payload.get("last_close"))
    add(payload.get("horizon_days"))
    for v in payload.get("forecast") or []:
        add(v)
    for v in (payload.get("metrics") or {}).values():
        add(v)

    forecast = payload.get("forecast") or []
    if len(forecast) >= 2 and forecast[0]:
        delta = forecast[-1] - forecast[0]
        add(delta)
        add(delta / forecast[0] * 100)  # percent change, first -> last

    allowed: set[float] = set()
    for v in raw:
        allowed.add(v)
        allowed.update(round(v, d) for d in range(3))  # 0-2 decimal variants
    return allowed


def check_numbers(text: str, payload: dict) -> dict:
    """Verify every number in `text` traces back to `payload` (± rounding)."""
    allowed = _allowed_numbers(payload)
    extracted = _extract_numbers(text)

    unmatched = [
        token
        for token, value in extracted
        if not any(abs(value - a) <= _TOLERANCE for a in allowed)
    ]

    return {
        "numbers_checked": len(extracted),
        "numbers_matched": len(extracted) - len(unmatched),
        "unmatched": unmatched,
        "passed": not unmatched,
    }
