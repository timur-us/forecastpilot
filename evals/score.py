"""Scorer for the commentary eval suite: seven pass/fail checks per sample.

Every metric measures what the prompt in app.commentary.SYSTEM demands and
nothing more. Where the prompt leaves a point open, the scorer takes the
lenient reading and records a hint instead of a failure; the report groups
the hints by spec gap (SPEC_GAPS) as input for the next prompt revision.

numbers_match  every number in the text traces back to the payload: the
               existing guard app.evals.check_numbers (precision).
language       target language only: its disclaimer present and the other
               one absent, no section label of the other language in label
               position, function words overwhelmingly of the target language.
structure      the three parts under the prompt's names, once each and in
               order, no extra numbered heading, the text ends with the
               disclaimer. One short title line before part 1 is a hint
               (G12); a paragraph there is a failure.
verdict        part 2 mentions the naive baseline and does not contradict
               beats_naive; without a backtest it says so and cites no
               metric figure. Keyword heuristic with negations (limits in
               the docstring of _classify).
coverage       part 1 cites the last close, the forecast start and the
               forecast end (completeness, the guard's counterpart), with
               the guard's number parsing and tolerance.
length         at most 150 words, labels and disclaimer included.
complete       stop_reason end_turn and a non-empty text.

Communication metrics (weaknesses the baseline showed within the contract):

jargon_free    no code in the prose: no snake_case identifiers such as
               beats_naive or mae_model, no True/False literals.
locale_numbers every number with a separator in the target language's
               format (DE 18.234,56 and 6,88; EN 18,234.56 and 6.88).
no_title_line  nothing before the part 1 label.
label_format   each label a line of exactly **Name** (TARGET_LABELS): bold,
               own line, exact name, no number, no colon.
"""

import re
from dataclasses import dataclass, field

# The guard's tokenizer and tolerance, so precision (numbers_match) and
# completeness (coverage) read numbers the same way.
from app.evals import _FLOAT_SLACK, _TOLERANCE, _extract_numbers, check_numbers

METRICS = (
    "numbers_match",
    "language",
    "structure",
    "verdict",
    "coverage",
    "length",
    "complete",
    "jargon_free",
    "locale_numbers",
    "no_title_line",
    "label_format",
)
# The seven contract metrics measure what the prompt demands. Four of them
# are correctness metrics: they must never fall between prompt versions
# (the regression gate in tests/test_evals_suite.py).
CONTRACT_METRICS = METRICS[:7]
CORRECTNESS_METRICS = ("numbers_match", "verdict", "coverage", "complete")
# The four communication metrics measure weaknesses the baseline showed
# although it met the contract: code in the prose, mixed number formats,
# title lines, varying label forms. They are metrics, not gates.
COMMUNICATION_METRICS = METRICS[7:]

# A version is judged by this rule, fixed before any version beyond the
# baseline was recorded.
SCORING_RULE = (
    "A prompt version counts as better than the previous one when the four "
    "communication metrics (jargon_free, locale_numbers, no_title_line, "
    "label_format) rise and no correctness metric (numbers_match, verdict, "
    "coverage, complete) falls, measured as case pass rates on the golden set; "
    "the hold-out set is reported alongside as a check that the rules were not "
    "tuned to the golden set."
)

# Metrics a prompt version satisfies by construction: the code renders the
# part, the model cannot get it wrong. The report marks them so they are not
# read as model performance.
BY_CONSTRUCTION = {
    "2026-10-25.3": {
        "label_format": "labels rendered by code",
        "no_title_line": "rendered by code",
        "structure": "labels, order and disclaimer rendered by code",
        "verdict": "verdict sentence rendered by code; a contradicting prose sentence still fails",
    },
}

# Versions that were recorded, measured and not shipped. They stay in the
# record so the regression stays visible; the gate and the Δ columns compare
# accepted versions only.
REJECTED_VERSIONS = {
    "2026-10-25.1": (
        "numbers_match fell from 20/20 to 16/20 golden cases (hold-out 6/6 to 3/6): "
        "English replies computed the change between forecast end and last close, "
        "a figure not in the data, and two replies restated part 1 after a visible "
        "self-correction. Replaced by 2026-10-25.2 with one added rule for part 1."
    ),
}

# Changes to the scorer, each with the sample that showed it measuring wrongly.
SCORER_CHANGELOG = (
    (
        "2026-10-25",
        "numbers_match (app.evals.check_numbers): an integer directly after an index name (S&P 500, Nasdaq 100, DAX 40) is the name, not a cited figure, and is no longer extracted.",
        "en-gspc-30-thousands-1/2/3 under 2026-10-25.2 and -3 under 2026-10-25.1: 'The S&P 500 (^GSPC) last closed at 5,812.34.' failed with 'numbers not in the data: 500'.",
    ),
    (
        "2026-10-24",
        "verdict: the English 'lags' in the subject-neutral negative list matched the German 'lag' (past tense of liegen); narrowed to 'lags behind'.",
        "de-mbg-30-narrow-win-2 under 2026-10-23.1: 'Im Backtest über 30 Tage lag der mittlere absolute Fehler (MAE) des Modells bei 1.4999, ...' was read as a negative verdict.",
    ),
)

# The target label format for the communication metric label_format, fixed
# here and prescribed by prompt v2: one bold line per label, the exact name,
# no number, no colon. The web app renders a bold-only line as a heading.
TARGET_LABELS = {
    "en": ("Price movement", "Model quality", "Interpretation"),
    "de": ("Kursbewegung", "Modellgüte", "Einschränkung"),
}

DISCLAIMERS = {
    "en": "Educational analysis — not investment advice.",
    "de": "Bildungsanalyse — keine Anlageberatung.",
}

# Section names as the prompt gives them ("Kursbewegung / Price movement",
# "Modellgüte / Model quality", "Einschränkung / Interpretation").
# "Interpretation" is the prompt's English name of part 3 and a German word,
# so it is a valid label in both languages and foreign in neither (G2).
# "Limitation" is accepted in English because the prompt pairs
# "Einschränkung" with "Interpretation" (G2).
LABELS = {
    "en": (
        ("price movement",),
        ("model quality",),
        ("interpretation", "limitation", "limitations"),
    ),
    "de": (
        ("kursbewegung",),
        ("modellgüte",),
        ("einschränkung", "einschränkungen", "interpretation"),
    ),
}

# Points the prompt leaves open. The scorer never counts them as model
# failures; where it can see them it records a hint with the gap's tag.
# CLOSED_BY names the prompt version whose rules settle a gap; the report
# marks it for versions from then on.
CLOSED_BY = {
    "G1": "2026-10-25.1",
    "G2": "2026-10-25.1",
    "G4": "2026-10-25.1",
    "G6": "2026-10-25.1",
    "G8": "2026-10-25.1",
    "G12": "2026-10-25.1",
}
SPEC_GAPS = (
    (
        "G1",
        "Section labels: the prompt names the parts but does not say whether the names are to be used verbatim, numbered, or set as headings. The scorer accepts the prompt's names in any form; another label word fails structure so the deviation stays visible.",
    ),
    (
        "G2",
        "Part 3 is paired as 'Einschränkung / Interpretation': 'Interpretation' is also a German word, and the natural English counterpart would be 'Limitation'. The scorer accepts 'Interpretation' in both languages and 'Limitation(s)' in English.",
    ),
    (
        "G3",
        "No word-counting rule. The scorer counts whitespace-separated tokens that contain a letter or digit, labels and disclaimer included; the count without them is reported as info.",
    ),
    (
        "G4",
        "Technical tokens in German text (MAE, MAPE, backtest, beats_naive, naive baseline): the prompt forbids mixing languages but hands these over in English. The language check ignores them.",
    ),
    (
        "G5",
        "The prompt forbids any rounding; the numbers guard tolerates 0-2 decimals and the derived difference and percent change. numbers_match uses the guard's tolerance.",
    ),
    (
        "G6",
        "German number formatting (1.234,56) is a re-formatting of a given number; the guard reads both conventions.",
    ),
    (
        "G7",
        "The disclaimer is quoted with an em dash and a final period; other dashes or punctuation are recorded as a hint, not a failure.",
    ),
    (
        "G8",
        "No wording is prescribed for the verdict on beats_naive or for 'the backtest could not be computed'. verdict uses keyword lists: a baseline sentence it cannot classify is recorded as a hint, whereas a no-backtest statement it does not recognise is a failure (the statement is required, so an unrecognised wording stays visible).",
    ),
    (
        "G9",
        "The horizon is given in trading days; restating it in weeks or months introduces a number that is not in the data and fails numbers_match.",
    ),
    (
        "G10",
        "No currency is given; the text may infer one from the ticker. Not measured.",
    ),
    (
        "G11",
        "Part 2 asks for 'backtest accuracy' without saying which of the metric values to cite, nor whether the note text may be quoted without a backtest. Not measured.",
    ),
    (
        "G12",
        "A title line before part 1 (e.g. the ticker and the word commentary) is neither allowed nor forbidden. structure tolerates one short line and records a hint.",
    ),
)


@dataclass
class Result:
    passed: bool
    reason: str = ""
    hints: list = field(default_factory=list)  # "G7: ..." strings
    info: dict = field(default_factory=dict)


def payload_for(case: dict) -> dict:
    """The dict the numbers guard reads, as generate_commentary builds it."""
    return {
        "ticker": case["ticker"],
        "last_close": case["last_close"],
        "forecast": case["forecast"],
        "metrics": case["metrics"],
        "horizon_days": len(case["forecast"]),
    }


# --- normalisation ---------------------------------------------------------

_DASH_RE = re.compile("[—–−]")  # em dash, en dash, minus sign
_MARKUP_RE = re.compile(r"[*#`]")


def normalize(text: str) -> str:
    """Markdown markers out, dashes unified, spaces collapsed; lines kept.

    Underscores stay so that `beats_naive` remains readable.
    """
    text = _DASH_RE.sub("-", _MARKUP_RE.sub("", text))
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def _lines(text: str) -> list[str]:
    return [line for line in normalize(text).splitlines() if line]


# Optional numbering before a label: "1.", "1)", "Part 1 -", "Teil 2:".
_PREFIX_RE = re.compile(
    r"^\s*(?:(?:part|teil)\s*)?(?:\d+\s*[.):\-]?\s*)?", re.IGNORECASE
)


def _label_prefix(line: str, names: tuple) -> int | None:
    """Length of the label prefix when `line` starts with one of `names`."""
    low = line.lower()
    start = _PREFIX_RE.match(low).end()
    rest = low[start:]
    for name in names:
        if rest.startswith(name):
            tail = rest[len(name) :]
            if tail and tail[0].isalpha():
                continue  # a longer word, not the label
            end = start + len(name)
            return end + re.match(r"\s*[:.\-)]*\s*", low[end:]).end()
    return None


def _label_lines(lines: list, lang: str) -> dict:
    """Line indices of the three parts' labels for `lang`."""
    found = {0: [], 1: [], 2: []}
    for i, line in enumerate(lines):
        for idx in range(3):
            if _label_prefix(line, LABELS[lang][idx]) is not None:
                found[idx].append(i)
    return found


def _section(lines: list, lang: str, idx: int) -> tuple:
    """Text of part `idx` with its label removed, or the whole text.

    Returns (text, scoped). scoped is False when the labels that delimit the
    part are not found; the caller then reads the whole text.
    """
    found = _label_lines(lines, lang)
    if not found[idx] or (idx < 2 and not found[idx + 1]):
        return "\n".join(lines), False
    start = found[idx][0]
    end = found[idx + 1][0] if idx < 2 else len(lines)
    if end <= start:
        return "\n".join(lines), False
    first = lines[start][_label_prefix(lines[start], LABELS[lang][idx]) :]
    body = [first] + lines[start + 1 : end]
    return "\n".join(line for line in body if line), True


def _disclaimer_key(lang: str) -> str:
    """The disclaimer as compared: normalised, lower case, no final period."""
    return normalize(DISCLAIMERS[lang]).lower().rstrip(".")


# --- metrics -----------------------------------------------------------------


def numbers_match(text: str, payload: dict) -> Result:
    guard = check_numbers(text, payload)
    reason = (
        ""
        if guard["passed"]
        else "numbers not in the data: " + ", ".join(guard["unmatched"])
    )
    return Result(guard["passed"], reason, info=guard)


_STOPWORDS = {
    # Function words of either language; words spelled the same in both
    # (in, an, so, was, also, will, man, fast, bald, per) are left out.
    "en": {
        "the",
        "and",
        "of",
        "to",
        "is",
        "are",
        "with",
        "for",
        "not",
        "than",
        "that",
        "this",
        "from",
        "by",
        "on",
        "at",
        "as",
        "it",
        "its",
        "over",
        "which",
        "while",
        "does",
        "do",
        "but",
        "or",
        "be",
        "been",
        "has",
        "have",
        "were",
        "would",
        "should",
        "could",
        "may",
        "can",
        "if",
        "then",
        "these",
        "those",
        "between",
        "into",
        "against",
        "about",
        "more",
        "less",
        "no",
        "only",
        "after",
        "before",
        "during",
        "any",
        "each",
        "both",
        "there",
        "their",
        "they",
        "we",
        "you",
        "our",
        "such",
        "same",
        "because",
        "however",
        "whether",
        "though",
        "although",
        "since",
        "until",
        "through",
        "above",
        "below",
        "within",
        "without",
        "a",
    },
    "de": {
        "der",
        "die",
        "das",
        "und",
        "ist",
        "sind",
        "nicht",
        "als",
        "mit",
        "für",
        "von",
        "auf",
        "den",
        "dem",
        "des",
        "ein",
        "eine",
        "einen",
        "einer",
        "eines",
        "im",
        "zu",
        "bei",
        "über",
        "unter",
        "zum",
        "zur",
        "sich",
        "wird",
        "werden",
        "wurde",
        "kann",
        "könnte",
        "sollte",
        "dass",
        "ob",
        "oder",
        "aber",
        "auch",
        "nur",
        "noch",
        "wie",
        "bis",
        "nach",
        "vor",
        "durch",
        "gegen",
        "keine",
        "kein",
        "dieser",
        "diese",
        "dieses",
        "zwischen",
        "etwa",
        "dabei",
        "jedoch",
        "damit",
        "dennoch",
        "somit",
        "sowie",
        "ohne",
        "um",
        "aus",
        "bleibt",
        "liegt",
        "hat",
        "haben",
        "sei",
        "seine",
        "seiner",
        "ihre",
        "ihrer",
        "ihr",
        "es",
        "er",
        "sie",
        "wir",
        "wenn",
        "weil",
        "da",
        "dann",
        "hier",
        "dort",
        "sehr",
        "mehr",
        "weniger",
        "deutlich",
        "leicht",
        "daher",
        "deshalb",
        "allerdings",
        "dagegen",
        "hingegen",
    },
}
_TECHNICAL_RE = re.compile(
    r"\b(?:mae|mape|backtest\w*|beats_naive|naiv\w*|baseline|basislinie|holt[- ]winters|ticker)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß]+")


def language(text: str, lang: str, ticker: str) -> Result:
    other = "de" if lang == "en" else "en"
    lines = _lines(text)
    low = "\n".join(lines).lower()
    if _disclaimer_key(lang) not in low:
        return Result(False, f"disclaimer in {lang} missing")
    if _disclaimer_key(other) in low:
        return Result(False, f"disclaimer in {other} present")

    own = {name for names in LABELS[lang] for name in names}
    foreign = tuple(
        name for names in LABELS[other] for name in names if name not in own
    )
    for line in lines:
        if _label_prefix(line, foreign) is not None:
            return Result(False, f"label in {other} in label position: {line!r}")
        starts_own = any(
            _label_prefix(line, names) is not None for names in LABELS[lang]
        )
        if starts_own and any(name in line.lower() for name in foreign):
            return Result(False, f"bilingual label: {line!r}")

    body = low.replace(_disclaimer_key(lang), " ")
    body = _TECHNICAL_RE.sub(" ", body).replace(ticker.lower(), " ")
    words = _WORD_RE.findall(body)
    hits = {code: sum(w in _STOPWORDS[code] for w in words) for code in ("en", "de")}
    info = {"function_words": hits}
    if hits[lang] < 5:
        return Result(False, f"only {hits[lang]} {lang} function words", info=info)
    share = hits[lang] / (hits[lang] + hits[other])
    if share < 0.9:
        return Result(
            False,
            f"{hits[other]} of {hits[lang] + hits[other]} function words are {other}",
            info=info,
        )
    return Result(True, info=info)


# "4. Fazit" is a heading; "2.77% MAPE" or "1.5 against 1.5001" are not.
_NUMBERED_HEADING_RE = re.compile(r"^\d{1,2}\s*[.)]\s*[A-Za-zÄÖÜäöü]")


def structure(text: str, lang: str) -> Result:
    lines = _lines(text)
    if not lines:
        return Result(False, "empty text")
    found = _label_lines(lines, lang)
    for idx in range(3):
        names = " / ".join(LABELS[lang][idx])
        if not found[idx]:
            return Result(False, f"part {idx + 1} label missing (expected {names})")
        if len(found[idx]) > 1:
            return Result(
                False, f"part {idx + 1} label appears {len(found[idx])} times"
            )
    order = [found[0][0], found[1][0], found[2][0]]
    if order != sorted(order):
        return Result(False, "parts out of order")

    hints = []
    before = lines[: order[0]]
    if before:
        if len(before) == 1 and len(before[0].split()) <= 10:
            hints.append(f"G12: title line before part 1: {before[0]!r}")
        else:
            return Result(False, "text before part 1")

    for i, line in enumerate(lines):
        if i in order:
            continue
        if _NUMBERED_HEADING_RE.match(line) and len(line.split()) <= 6:
            return Result(False, f"extra numbered heading: {line!r}")

    last = lines[-1].lower().rstrip(" \"'.")
    if not last.endswith(_disclaimer_key(lang)):
        return Result(False, "does not end with the disclaimer", hints=hints)
    if DISCLAIMERS[lang] not in text:
        hints.append("G7: disclaimer with a different dash or punctuation")
    return Result(True, hints=hints)


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_LITERAL_RE = re.compile(
    r"beats_naive\s*(?:[:=]+|is|ist|->)?\s*\(?\s*(true|false|wahr|falsch|yes|no|ja|nein)\b",
    re.IGNORECASE,
)
_BASE_RE = re.compile(
    r"\b(?:naiv\w*|baseline|basislinie|fortschreibung|beats_naive|vergleichsmodell\w*|last[- ]value)\b",
    re.IGNORECASE,
)
_SUBJECT_RE = re.compile(
    r"(?P<base>\b(?:naiv\w*|baseline|basislinie|fortschreibung|vergleichsmodell\w*|last[- ]value)\b)"
    r"|(?P<model>\b(?:model|modell\w*|holt[- ]winters)\b)",
    re.IGNORECASE,
)
_NAIVE_MODEL_RE = re.compile(r"\b(naiv\w*)\s+(?:model\w*|modell\w*)\b", re.IGNORECASE)
_POS_RE = re.compile(
    r"\b(?:beats?|outperform(?:s|ed|ing)?|better|more accurate|improves? (?:on|upon)"
    r"|schlägt|übertrifft|besser|genauer|superior)\b",
    re.IGNORECASE,
)
_NEG_RE = re.compile(
    r"\b(?:not|no|nor|never|fails?|neither|nicht|kein\w*|weder)\b|n't", re.IGNORECASE
)
_NEUTRAL_NEG_RE = re.compile(
    r"\b(?:worse|underperform\w*|lags? behind|trails?|schlechter|ungenauer|unterliegt|unterlegen|verfehlt)\b"
    r"|bleibt hinter",
    re.IGNORECASE,
)
_NEUTRAL_POS_RE = re.compile(r"\b(?:geschlagen|übertroffen|überlegen)\b", re.IGNORECASE)
# "is outperformed by the naive baseline", "wird von der naiven Baseline geschlagen".
_PASSIVE_RE = re.compile(
    r"\b(?:outperformed|beaten|surpassed|übertroffen|geschlagen|überholt)\s+(?:by|von|durch)\b"
    r"|\b(?:von|durch)\s+(?:der|dem|die)\s+naiv\w*\b[^.]*\b(?:übertroffen|geschlagen|überholt)\b",
    re.IGNORECASE,
)
# Clause boundaries for the negation check: a negation in "..., though not by
# much" must not flip the verdict in the clause before the comma.
_CLAUSE_RE = re.compile(
    r"[,;:()]|\b(?:though|although|but|yet|whereas|aber|obwohl|wenngleich|allerdings|jedoch|doch|wobei)\b",
    re.IGNORECASE,
)
_NO_BACKTEST_RE = {
    "en": re.compile(
        r"could not be (?:computed|calculated|performed|run|assessed|evaluated)"
        r"|cannot be (?:computed|calculated|assessed|evaluated)"
        r"|(?:was|is) not (?:computed|possible|available)|not be computed"
        r"|no backtest|without (?:a )?backtest|unavailable|too short"
        r"|insufficient (?:data|history)|not available|not possible"
        r"|(?:could|can|cannot|can't) ?not be (?:computed|calculated|assessed|evaluated|determined)"
        r"|(?:no|without) (?:backtest )?(?:metrics|figures|results)",
        re.IGNORECASE,
    ),
    "de": re.compile(
        r"konnten? nicht (?:berechnet|durchgeführt|ermittelt|bewertet)"
        r"|nicht berechnet|nicht möglich|kein backtest|ohne backtest"
        r"|nicht verfügbar|zu kurz|lieg(?:t|en) nicht vor|nicht ermittelt"
        r"|nicht durchgeführt|lässt sich nicht|nicht bewertet|keine?n? backtest"
        r"|nicht vorhanden|nicht vorliegen|lieg(?:t|en) keine?|kann nicht (?:berechnet|bewertet|beurteilt)"
        r"|keine (?:backtest-?)?(?:kennzahlen|werte|metriken|ergebnisse)",
        re.IGNORECASE,
    ),
}
_METRIC_WORD_RE = re.compile(
    r"\b(?:mae|mape|error|fehler|genauigkeit|accuracy|abweichung)\b", re.IGNORECASE
)


def _classify(sentence: str) -> str | None:
    """'pos' (model beats the baseline), 'neg' (it does not) or None.

    Order: the literal `beats_naive: true/false` wins; then a positive token
    ("beats", "better", "schlägt", "genauer", ...) is read with the nearest
    subject before it (model → pos, baseline → neg, none → pos as in "beats
    the naive baseline"); then subject-neutral words ("worse", "schlechter",
    "unterliegt" → neg; "geschlagen", "übertroffen" → pos). A negation token
    in the sentence flips the result. Direction words such as "lower" or
    "higher" are deliberately not in the lists: their meaning depends on
    what is being compared.

    The negation is looked for in the clause that holds the verdict token
    (split at commas, parentheses and concessive conjunctions), so "beats the
    baseline, though not by much" stays positive. A passive ("is outperformed
    by the naive baseline", "wird von der naiven Baseline geschlagen") is
    read as negative.

    Limits: a pronoun subject ("it is better than ..."), a dative object
    before "überlegen" in the reversed order ("Die Baseline ist dem Modell
    überlegen" reads as positive), and comparisons spread over two sentences.
    """
    s = sentence.lower()
    literal = _LITERAL_RE.search(s)
    if literal:
        return "pos" if literal.group(1) in {"true", "wahr", "yes", "ja"} else "neg"
    s = _NAIVE_MODEL_RE.sub(r"\1", s)  # "naive model" is the baseline
    passive = _PASSIVE_RE.search(s)
    if passive:
        return "pos" if _negated_around(s, passive.start()) else "neg"
    pos = _POS_RE.search(s)
    if pos:
        before = [
            m.lastgroup for m in _SUBJECT_RE.finditer(s) if m.start() < pos.start()
        ]
        verdict = "pos" if not before or before[-1] == "model" else "neg"
        return _flip(verdict) if _negated_around(s, pos.start()) else verdict
    neutral_neg = _NEUTRAL_NEG_RE.search(s)
    if neutral_neg:
        return "pos" if _negated_around(s, neutral_neg.start()) else "neg"
    neutral_pos = _NEUTRAL_POS_RE.search(s)
    if neutral_pos:
        return "neg" if _negated_around(s, neutral_pos.start()) else "pos"
    return None


def _negated_around(sentence: str, position: int) -> bool:
    """Is there a negation token in the clause that contains `position`?"""
    start, end = 0, len(sentence)
    for boundary in _CLAUSE_RE.finditer(sentence):
        if boundary.start() <= position:
            start = boundary.end()
        else:
            end = boundary.start()
            break
    return _NEG_RE.search(sentence[start:end]) is not None


def _flip(verdict: str) -> str:
    return "neg" if verdict == "pos" else "pos"


def _sentences(text: str) -> list:
    return [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]


def verdict(text: str, lang: str, expected: str, payload: dict) -> Result:
    lines = _lines(text)
    part2, scoped = _section(lines, lang, 1)
    info = {"scoped_to_part_2": scoped}

    if expected == "no_backtest":
        whole = "\n".join(lines)
        # The statement belongs to part 2 (the prompt puts it there); a cited
        # metric figure is wrong anywhere in the text.
        if not _NO_BACKTEST_RE[lang].search(part2):
            return Result(
                False, "does not say that the backtest could not be computed", info=info
            )
        for sentence in _sentences(whole):
            if _METRIC_WORD_RE.search(sentence):
                unmatched = check_numbers(sentence, payload)["unmatched"]
                if unmatched:
                    return Result(
                        False,
                        f"cites a metric figure without a backtest: {sentence!r}",
                        info=info,
                    )
        claimed = [s for s in _sentences(part2) if _BASE_RE.search(s) and _classify(s)]
        info["verdict_sentences"] = claimed
        return Result(True, info=info)

    sentences = [s for s in _sentences(part2) if _BASE_RE.search(s)]
    if not sentences:
        return Result(
            False, "no statement about the naive baseline in part 2", info=info
        )
    classified = {s: _classify(s) for s in sentences}
    info["verdict_sentences"] = classified
    contrary = "neg" if expected == "beats" else "pos"
    wrong = [s for s, v in classified.items() if v == contrary]
    if wrong:
        return Result(
            False, f"contradicts beats_naive ({expected}): {wrong[0]!r}", info=info
        )
    hints = []
    if not any(classified.values()):
        hints.append(f"G8: baseline sentence not classifiable: {sentences[0]!r}")
    return Result(True, hints=hints, info=info)


def coverage(text: str, lang: str, payload: dict) -> Result:
    lines = _lines(text)
    part1, scoped = _section(lines, lang, 0)
    candidates = [c for _token, cs in _extract_numbers(part1) for c in cs]
    forecast = payload["forecast"]
    targets = {
        "last close": float(payload["last_close"]),
        "forecast start": float(forecast[0]),
        "forecast end": float(forecast[-1]),
    }
    missing = [name for name, value in targets.items() if not _cited(value, candidates)]
    info = {
        "scoped_to_part_1": scoped,
        "cited": [n for n in targets if n not in missing],
    }
    if missing:
        return Result(False, "part 1 does not cite: " + ", ".join(missing), info=info)
    return Result(True, info=info)


def _cited(value: float, candidates: list) -> bool:
    """Same tolerance as the guard: the value or its 0-2 decimal roundings."""
    variants = {value, *(round(value, d) for d in range(3))}
    # abs(): a range written with a dash ("221.78-226.34") reads as a negative
    # number after normalisation; prices are never negative.
    return any(
        abs(abs(c) - v) <= _TOLERANCE + _FLOAT_SLACK
        for c in candidates
        for v in variants
    )


_WORD_CHAR_RE = re.compile(r"[0-9A-Za-zÄÖÜäöüß]")


def _count_words(text: str) -> int:
    return sum(1 for token in text.split() if _WORD_CHAR_RE.search(token))


def length(text: str, lang: str, max_words: int) -> Result:
    lines = _lines(text)
    total = _count_words("\n".join(lines))
    stripped = []
    for line in lines:
        for idx in range(3):
            prefix = _label_prefix(line, LABELS[lang][idx])
            if prefix is not None:
                line = line[prefix:]
                break
        stripped.append(line)
    body = re.sub(
        re.escape(_disclaimer_key(lang)), " ", "\n".join(stripped), flags=re.IGNORECASE
    )
    info = {"words": total, "words_without_labels_and_disclaimer": _count_words(body)}
    if total > max_words:
        return Result(False, f"{total} words, limit {max_words}", info=info)
    return Result(True, info=info)


def complete(
    text: str | None, stop_reason: str | None, error: str | None = None
) -> Result:
    if error:
        return Result(False, f"no reply: {error}")
    if stop_reason != "end_turn":
        return Result(False, f"stop_reason {stop_reason}")
    if not (text or "").strip():
        return Result(False, "empty text")
    return Result(True)


# --- communication metrics ---------------------------------------------------

# snake_case identifiers (beats_naive, mae_model), Python literals, braces and
# backticks are code; MAE, MAPE and backtest are vocabulary and stay allowed.
_CODE_RE = re.compile(r"\b[A-Za-z]+_[A-Za-z_]+\b|\b(?:True|False|None)\b|[{}`]")


def jargon_free(text: str) -> Result:
    found = sorted({m.group() for m in _CODE_RE.finditer(text)})
    if found:
        return Result(
            False, "code in the text: " + ", ".join(found), info={"code": found}
        )
    return Result(True)


_SEP_NUMBER_RE = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)+(?![\w])")


def _number_locale(token: str) -> str | None:
    """'de' (18.234,56 / 6,88), 'en' (18,234.56 / 6.88) or None when ambiguous."""
    if "." in token and "," in token:
        return "de" if token.rfind(",") > token.rfind(".") else "en"
    sep = "," if "," in token else "."
    parts = token.split(sep)
    if len(parts) > 2:  # 1.234.567: grouping only
        return "de" if sep == "." else "en"
    head, tail = parts
    if len(tail) == 3 and len(head) <= 3:
        return None  # 1.500 / 1,500: a thousand or a decimal, cannot tell
    return "de" if sep == "," else "en"


def locale_numbers(text: str, lang: str) -> Result:
    """Every number with a separator is in the target language's format."""
    judged = {token: _number_locale(token) for token in _SEP_NUMBER_RE.findall(text)}
    wrong = sorted(
        token for token, locale in judged.items() if locale and locale != lang
    )
    info = {"numbers": judged}
    if wrong:
        return Result(
            False, f"not {lang} number format: " + ", ".join(wrong), info=info
        )
    return Result(True, info=info)


def no_title_line(text: str, lang: str) -> Result:
    lines = _lines(text)
    found = _label_lines(lines, lang)
    if not found[0]:
        return Result(False, "part 1 label not found")
    before = lines[: found[0][0]]
    if before:
        return Result(False, f"line before part 1: {before[0]!r}")
    return Result(True)


def label_format(text: str, lang: str) -> Result:
    """Each label is a line of exactly **Name**: bold, own line, no number."""
    raw = [line.strip() for line in text.splitlines() if line.strip()]
    expected = [f"**{name}**" for name in TARGET_LABELS[lang]]
    missing = [e for e in expected if e not in raw]
    if not missing:
        return Result(True)
    lines = _lines(text)
    found = _label_lines(lines, lang)
    written = []
    for idx in range(3):
        if expected[idx] in raw or not found[idx]:
            continue
        line_index = found[idx][0]
        written.append(_raw_line(text, lines[line_index]))
    reason = "expected " + ", ".join(missing)
    if written:
        reason += "; written as " + ", ".join(repr(w[:50]) for w in written)
    return Result(False, reason, info={"written": written})


def _raw_line(text: str, normalized_line: str) -> str:
    """The raw line that normalises to `normalized_line` (first match)."""
    for line in text.splitlines():
        if normalize(line) == normalized_line:
            return line.strip()
    return normalized_line


def score(
    case: dict, text: str | None, stop_reason: str | None, error: str | None = None
) -> dict:
    """Score one sample against its case: {metric: Result}."""
    lang = case["expect"]["language"]
    payload = payload_for(case)
    results = {"complete": complete(text, stop_reason, error)}
    if not (text or "").strip():
        for metric in METRICS:
            results.setdefault(metric, Result(False, "no text"))
        return {metric: results[metric] for metric in METRICS}
    results["numbers_match"] = numbers_match(text, payload)
    results["language"] = language(text, lang, case["ticker"])
    results["structure"] = structure(text, lang)
    results["verdict"] = verdict(text, lang, case["expect"]["verdict"], payload)
    results["coverage"] = coverage(text, lang, payload)
    results["length"] = length(text, lang, case["expect"]["max_words"])
    results["jargon_free"] = jargon_free(text)
    results["locale_numbers"] = locale_numbers(text, lang)
    results["no_title_line"] = no_title_line(text, lang)
    results["label_format"] = label_format(text, lang)
    return {metric: results[metric] for metric in METRICS}
