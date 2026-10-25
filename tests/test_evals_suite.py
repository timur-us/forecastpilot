"""Offline tests for the eval suite (evals/): the scorer must detect every
failure mode on artificial answers, pass the two real replies, and build the
report from the recorded baseline. No network, no API keys."""

import json
from collections import Counter

import pytest

from evals import run, score
from evals.score import (
    COMMUNICATION_METRICS,
    CONTRACT_METRICS,
    CORRECTNESS_METRICS,
    METRICS,
)

CASES = {c["id"]: c for c in run.load_cases(sets=("golden",))}
HOLDOUT = {c["id"]: c for c in run.load_cases(sets=("holdout",))}

# Live reply of the deployed service, README example (NVDA, 30 days, en).
NVDA_TEXT = """**1. Price movement**
NVDA last closed at 221.97. The forecast starts at 221.78 and ends at 226.34 after 30 trading days, which is above the last close.

**2. Model quality**
Over a 30-day backtest horizon, the model's MAE was 6.1651 versus 6.4573 for the naive baseline. Its MAPE was 2.77%. beats_naive is True, so the model beat the naive baseline, though the margin is narrow.

**3. Interpretation**
The forecast is a statistical projection from past price patterns. It does not account for earnings, news, or market shocks. A backtest edge over the naive baseline does not guarantee future accuracy. The forecast should be read as one input among several, not as a prediction of the outcome.

Educational analysis — not investment advice."""

# Live reply of the deployed service, earlier README example (SAP.DE, 5 days, de).
SAP_CASE = {
    "id": "sap-readme",
    "ticker": "SAP.DE",
    "language": "de",
    "horizon": 5,
    "last_close": 201.55,
    "forecast": [201.44, 201.43, 201.42, 201.41, 201.4],
    "metrics": {
        "backtest_horizon": 5,
        "mae_model": 2.7679,
        "mae_naive": 2.616,
        "mape_model_pct": 1.38,
        "beats_naive": False,
    },
    "expect": {
        "language": "de",
        "verdict": "does_not_beat",
        "disclaimer": "Bildungsanalyse — keine Anlageberatung.",
        "max_words": 150,
    },
}
SAP_TEXT = """**Kursbewegung:** SAP.DE schloss zuletzt bei 201,55. Die Prognose startet bei 201,44 und endet nach 5 Handelstagen bei 201,40. Das entspricht einer praktisch unveränderten Kursentwicklung.

**Modellgüte:** Im Backtest (Horizont: 5 Tage) liegt der mittlere absolute Fehler des Modells bei 2,7679, der des naiven Vergleichsmodells bei 2,616. Der MAPE des Modells beträgt 1,38 %. Das Modell schlägt die naive Baseline nicht (beats_naive: False).

**Einschränkung:** Die Prognose deutet keine nennenswerte Kursänderung an. Da das Modell im Backtest nicht besser abschneidet als die naive Fortschreibung, hat sie nur begrenzten Zusatznutzen. Sie ist keine Aussage über tatsächliche künftige Kurse oder Risiken.

Bildungsanalyse — keine Anlageberatung."""

NVDA = CASES["en-nvda-30-beats"]
NO_BACKTEST = CASES["en-xyz-30-no-backtest"]


def _score(case, text, stop_reason="end_turn"):
    return score.score(case, text, stop_reason)


def _failed(results):
    return {metric for metric, r in results.items() if not r.passed}


def _hints(results):
    return [h for r in results.values() for h in r.hints]


# --- the real replies pass ---------------------------------------------------


def test_real_english_reply_meets_the_contract_without_hints():
    results = _score(NVDA, NVDA_TEXT)

    assert _failed(results) & set(CONTRACT_METRICS) == set()
    assert _hints(results) == []
    assert results["verdict"].info["scoped_to_part_2"] is True
    assert results["coverage"].info["cited"] == [
        "last close",
        "forecast start",
        "forecast end",
    ]
    assert results["length"].info["words"] == 121
    # The weaknesses the communication metrics were built for:
    assert results["jargon_free"].reason == "code in the text: True, beats_naive"
    assert results["label_format"].reason.startswith(
        "expected **Price movement**, **Model quality**, **Interpretation**; "
        "written as '**1. Price movement**'"
    )
    assert results["locale_numbers"].passed is True
    assert results["no_title_line"].passed is True


def test_real_german_reply_meets_the_contract_without_hints():
    results = _score(SAP_CASE, SAP_TEXT)

    assert _failed(results) & set(CONTRACT_METRICS) == set()
    assert _hints(results) == []
    assert results["language"].info["function_words"]["en"] == 0
    assert results["jargon_free"].reason == "code in the text: False, beats_naive"
    assert results["label_format"].reason.startswith(
        "expected **Kursbewegung**, **Modellgüte**, **Einschränkung**; "
        "written as '**Kursbewegung:** SAP.DE schloss zuletzt bei 201,5'"
    )
    assert results["locale_numbers"].passed is True
    assert results["no_title_line"].passed is True


def test_metric_groups_partition_the_metrics():
    assert set(CONTRACT_METRICS) | set(COMMUNICATION_METRICS) == set(METRICS)
    assert set(CORRECTNESS_METRICS) < set(CONTRACT_METRICS)
    assert not set(CONTRACT_METRICS) & set(COMMUNICATION_METRICS)


def test_hold_out_cases_are_separate_and_cover_the_required_shapes():
    assert len(HOLDOUT) == 6 and not set(HOLDOUT) & set(CASES)
    languages = [c["language"] for c in HOLDOUT.values()]
    assert languages.count("en") == 3 and languages.count("de") == 3
    assert any(c["expect"]["verdict"] == "no_backtest" for c in HOLDOUT.values())
    assert any(
        c["language"] == "de" and c["last_close"] > 10_000 for c in HOLDOUT.values()
    )
    for case in HOLDOUT.values():
        assert case["set"] == "holdout" and case["horizon"] == len(case["forecast"])


def test_case_files_cover_the_mandatory_edge_cases():
    assert len(CASES) == 20
    languages = [c["language"] for c in CASES.values()]
    assert languages.count("en") == 10 and languages.count("de") == 10
    assert {c["horizon"] for c in CASES.values()} == {5, 30, 90}
    verdicts = [c["expect"]["verdict"] for c in CASES.values()]
    assert verdicts.count("no_backtest") == 2
    assert "beats" in verdicts and "does_not_beat" in verdicts
    assert any(
        c["last_close"] > 10_000 and c["language"] == "de" for c in CASES.values()
    )
    assert any(c["last_close"] < 1 for c in CASES.values())
    assert any(c["metrics"].get("mae_model") == 6.8767 for c in CASES.values())
    assert any(c["metrics"].get("mape_model_pct", 0) > 30 for c in CASES.values())
    assert any(
        c["forecast"][0] == c["forecast"][-1] == c["last_close"] for c in CASES.values()
    )
    assert any(
        c["forecast"][-1] < c["forecast"][0] < c["last_close"] for c in CASES.values()
    )
    for case in CASES.values():
        assert case["horizon"] == len(case["forecast"])
        assert case["expect"]["language"] == case["language"]
        assert case["expect"]["disclaimer"] == score.DISCLAIMERS[case["language"]]
        metrics = case["metrics"]
        if "beats_naive" in metrics:
            assert set(metrics) == {
                "backtest_horizon",
                "mae_model",
                "mae_naive",
                "mape_model_pct",
                "beats_naive",
            }
            assert metrics["backtest_horizon"] == case["horizon"]
            assert metrics["beats_naive"] is (
                metrics["mae_model"] < metrics["mae_naive"]
            )
            assert all(
                isinstance(metrics[k], float)
                for k in ("mae_model", "mae_naive", "mape_model_pct")
            )
            assert case["expect"]["verdict"] == (
                "beats" if metrics["beats_naive"] else "does_not_beat"
            )
        else:
            assert metrics == {"note": "series too short for backtest"}
            assert case["expect"]["verdict"] == "no_backtest"


# --- every metric detects its failure case ------------------------------------


def test_language_fails_on_the_other_language():
    results = _score(NVDA, SAP_TEXT)

    assert "language" in _failed(results)
    assert "disclaimer in en missing" in results["language"].reason


def test_language_fails_on_a_bilingual_label():
    text = NVDA_TEXT.replace(
        "**1. Price movement**", "**1. Price movement / Kursbewegung**"
    )
    results = _score(NVDA, text)

    assert results["language"].passed is False
    assert "bilingual label" in results["language"].reason
    assert results["structure"].passed is True


def test_language_fails_on_a_label_in_the_other_language():
    text = NVDA_TEXT.replace("**2. Model quality**", "**2. Modellgüte**")
    results = _score(NVDA, text)

    assert "label in de in label position" in results["language"].reason
    assert "part 2 label missing" in results["structure"].reason


def test_language_ignores_foreign_words_in_running_text_and_technical_tokens():
    text = NVDA_TEXT.replace(
        "though the margin is narrow", "though the margin is narrow (Modellgüte: knapp)"
    )
    results = _score(NVDA, text)

    assert results["language"].passed is True


def test_language_fails_on_mostly_foreign_function_words():
    text = NVDA_TEXT.replace(
        "The forecast is a statistical projection from past price patterns.",
        "Die Prognose ist eine statistische Projektion aus der Kursentwicklung und "
        "nicht mehr als das, auch wenn sie mit den Daten gut übereinstimmt und "
        "für den Zeitraum der Prognose plausibel wirkt.",
    )
    results = _score(NVDA, text)

    assert results["language"].passed is False
    assert "function words are de" in results["language"].reason


def test_structure_fails_on_a_missing_part():
    text = NVDA_TEXT.replace("**3. Interpretation**\n", "")
    results = _score(NVDA, text)

    assert results["structure"].reason.startswith("part 3 label missing")


def test_structure_fails_on_a_label_that_is_not_in_the_prompt():
    text = NVDA_TEXT.replace("**1. Price movement**", "**1. Price action**")
    results = _score(NVDA, text)

    assert results["structure"].reason.startswith("part 1 label missing")


def test_structure_accepts_the_english_label_limitation_and_unnumbered_labels():
    text = (
        NVDA_TEXT.replace("**1. Price movement**", "Price movement:")
        .replace("**2. Model quality**", "Model quality:")
        .replace("**3. Interpretation**", "Limitations:")
    )
    results = _score(NVDA, text)

    assert results["structure"].passed is True
    assert results["language"].passed is True


def test_structure_tolerates_one_title_line_with_a_hint():
    results = _score(NVDA, "**NVDA – Commentary**\n\n" + NVDA_TEXT)

    assert results["structure"].passed is True
    assert results["structure"].hints == [
        "G12: title line before part 1: 'NVDA - Commentary'"
    ]


def test_structure_fails_on_a_paragraph_before_part_1():
    intro = "Here is the short commentary you asked for, written from the data provided.\n\n"
    results = _score(NVDA, intro + NVDA_TEXT)

    assert results["structure"].reason == "text before part 1"


def test_structure_fails_on_an_extra_numbered_heading():
    text = NVDA_TEXT.replace(
        "\nEducational analysis",
        "\n**4. Conclusion**\nThe model is usable.\n\nEducational analysis",
    )
    results = _score(NVDA, text)

    assert results["structure"].reason == "extra numbered heading: '4. Conclusion'"


def test_structure_does_not_read_a_short_line_with_a_decimal_as_a_heading():
    text = NVDA_TEXT.replace("Its MAPE was 2.77%.", "MAPE:\n2.77% over 30 days.")
    results = _score(NVDA, text)

    assert results["structure"].passed is True


def test_structure_fails_when_the_disclaimer_is_not_last():
    text = NVDA_TEXT.replace("\n\nEducational analysis — not investment advice.", "")
    text = text.replace(
        "**3. Interpretation**",
        "Educational analysis — not investment advice.\n\n**3. Interpretation**",
    )
    results = _score(NVDA, text)

    assert results["structure"].reason == "does not end with the disclaimer"


def test_structure_fails_on_duplicate_and_misordered_labels():
    swapped = (
        NVDA_TEXT.replace("**1. Price movement**", "**X**")
        .replace("**2. Model quality**", "**1. Price movement**")
        .replace("**X**", "**2. Model quality**")
    )
    duplicate = NVDA_TEXT.replace(
        "**3. Interpretation**", "**3. Interpretation**\nInterpretation: see below."
    )

    assert _score(NVDA, swapped)["structure"].reason == "parts out of order"
    assert _score(NVDA, duplicate)["structure"].reason == "part 3 label appears 2 times"


def test_disclaimer_with_another_dash_passes_with_a_hint():
    text = NVDA_TEXT.replace("Educational analysis — not", "Educational analysis - not")
    results = _score(NVDA, text)

    assert results["structure"].passed is True
    assert results["language"].passed is True
    assert results["structure"].hints == [
        "G7: disclaimer with a different dash or punctuation"
    ]


def test_verdict_fails_on_a_contradiction():
    text = NVDA_TEXT.replace(
        "beats_naive is True, so the model beat the naive baseline",
        "beats_naive is False, so the model does not beat the naive baseline",
    )
    results = _score(NVDA, text)

    assert results["verdict"].reason.startswith("contradicts beats_naive (beats)")


def test_verdict_fails_when_the_baseline_is_not_mentioned():
    text = NVDA_TEXT.replace(
        "Over a 30-day backtest horizon, the model's MAE was 6.1651 versus 6.4573 for the naive baseline. "
        "Its MAPE was 2.77%. beats_naive is True, so the model beat the naive baseline, though the margin is narrow.",
        "Over a 30-day backtest horizon, the model's MAE was 6.1651 and its MAPE 2.77%.",
    )
    results = _score(NVDA, text)

    assert (
        results["verdict"].reason == "no statement about the naive baseline in part 2"
    )


def test_verdict_passes_an_unclassifiable_baseline_sentence_with_a_hint():
    text = NVDA_TEXT.replace(
        "beats_naive is True, so the model beat the naive baseline, though the margin is narrow.",
        "The model's MAE of 6.1651 is lower than 6.4573 for the naive baseline.",
    )
    results = _score(NVDA, text)

    assert results["verdict"].passed is True
    assert (
        results["verdict"].hints[0].startswith("G8: baseline sentence not classifiable")
    )


def test_verdict_ignores_part_3_caveats_about_the_baseline():
    # The real reply's part 3 says "A backtest edge over the naive baseline
    # does not guarantee future accuracy" — a caveat, not a verdict.
    assert _score(NVDA, NVDA_TEXT)["verdict"].passed is True


@pytest.mark.parametrize(
    "sentence, expected",
    [
        ("beats_naive is True, so the model beat the naive baseline.", "pos"),
        ("Das Modell schlägt die naive Baseline nicht (beats_naive: False).", "neg"),
        (
            "Da das Modell nicht besser abschneidet als die naive Fortschreibung, ist der Nutzen begrenzt.",
            "neg",
        ),
        ("Das Modell schneidet schlechter ab als die naive Baseline.", "neg"),
        ("The model underperforms the naive baseline.", "neg"),
        ("The naive baseline was more accurate than the model.", "neg"),
        ("Die naive Baseline war mit 2,616 genauer als das Modell.", "neg"),
        ("Das Modell hat die naive Baseline geschlagen.", "pos"),
        ("Das Modell hat die naive Baseline nicht geschlagen.", "neg"),
        ("Compared with the naive baseline, the model is better.", "pos"),
        ("The naive model performed better than our model.", "neg"),
        ("The model beats the naive baseline by a narrow margin.", "pos"),
        ("The model's MAE was 6.1651 versus 6.4573 for the naive baseline.", None),
        # German "lag" (past tense of "liegen") is not the English "lags".
        (
            "Im Backtest lag der MAE des Modells bei 1.4999, der der naiven Basisprognose bei 1.5.",
            None,
        ),
        ("The model lags behind the naive baseline.", "neg"),
        ("The model beats the naive baseline, though not by much.", "pos"),
        ("The model is outperformed by the naive baseline.", "neg"),
        ("Das Modell wird von der naiven Baseline geschlagen.", "neg"),
        ("Das Modell wird von der naiven Baseline nicht geschlagen.", "pos"),
        ("Das Modell ist der naiven Baseline überlegen.", "pos"),
        ("Das Modell ist der naiven Baseline unterlegen.", "neg"),
    ],
)
def test_verdict_sentence_classification(sentence, expected):
    assert score._classify(sentence) == expected


def test_verdict_without_backtest_requires_the_statement_and_no_metric_figure():
    clean = """**1. Price movement**
XYZ last closed at 42.17. The forecast starts at 42.30 and ends at 44.05 after 30 trading days.

**2. Model quality**
The backtest could not be computed because the series is too short, so no error figures are available for the 30-day horizon.

**3. Interpretation**
Without a backtest the forecast is unvalidated and should be read with caution.

Educational analysis — not investment advice."""
    cites = clean.replace(
        "so no error figures are available for the 30-day horizon",
        "but the model's MAE of 1.25 compares with 1.40 for the naive baseline",
    )
    silent = clean.replace(
        "The backtest could not be computed because the series is too short, so no error figures are available for the 30-day horizon.",
        "The model fits the recent trend well and the error over the 30-day horizon is small.",
    )

    assert _failed(_score(NO_BACKTEST, clean)) & set(CONTRACT_METRICS) == set()
    assert "cites a metric figure" in _score(NO_BACKTEST, cites)["verdict"].reason
    assert (
        _score(NO_BACKTEST, silent)["verdict"].reason
        == "does not say that the backtest could not be computed"
    )


def test_verdict_without_backtest_in_german():
    case = CASES["de-xyz-30-no-backtest"]
    clean = """**Kursbewegung:** XYZ.DE schloss zuletzt bei 15,42. Die Prognose beginnt bei 15,30 und endet nach 30 Handelstagen bei 14,85.

**Modellgüte:** Der Backtest konnte nicht berechnet werden, da die Zeitreihe zu kurz ist. Eine Aussage zur Genauigkeit über 30 Handelstage ist daher nicht möglich.

**Einschränkung:** Ohne Backtest ist die Prognose nicht validiert und nur mit Vorsicht zu lesen.

Bildungsanalyse — keine Anlageberatung."""
    other_wording = clean.replace(
        "Der Backtest konnte nicht berechnet werden, da die Zeitreihe zu kurz ist.",
        "Es liegen keine Backtest-Kennzahlen vor.",
    )
    cites = clean.replace(
        "Eine Aussage zur Genauigkeit über 30 Handelstage ist daher nicht möglich.",
        "Der MAE des Modells liegt bei 0,42 gegenüber 0,51 für die naive Baseline.",
    )
    silent = clean.replace(
        "Der Backtest konnte nicht berechnet werden, da die Zeitreihe zu kurz ist. "
        "Eine Aussage zur Genauigkeit über 30 Handelstage ist daher nicht möglich.",
        "Das Modell folgt dem jüngsten Trend und der Fehler über 30 Handelstage ist klein.",
    )

    assert _failed(_score(case, clean)) & set(CONTRACT_METRICS) == set()
    assert _score(case, other_wording)["verdict"].passed is True
    assert "cites a metric figure" in _score(case, cites)["verdict"].reason
    assert _score(case, silent)["verdict"].reason == (
        "does not say that the backtest could not be computed"
    )


def test_interpretation_is_a_valid_label_in_both_languages():
    # G2: the prompt pairs "Einschränkung / Interpretation"; "Interpretation"
    # is also a German word. The recorded baseline uses all of these forms.
    de_single = SAP_TEXT.replace("**Einschränkung:**", "**Interpretation:**")
    de_pair = SAP_TEXT.replace(
        "**Einschränkung:**", "**Einschränkung / Interpretation:**"
    )
    de_plural = SAP_TEXT.replace("**Einschränkung:**", "**Einschränkungen:**")
    en_pair = NVDA_TEXT.replace(
        "**3. Interpretation**", "**3. Limitations / Interpretation**"
    )
    en_foreign = NVDA_TEXT.replace(
        "**3. Interpretation**", "**3. Einschränkung / Interpretation**"
    )

    for text in (de_single, de_pair, de_plural):
        results = _score(SAP_CASE, text)
        assert _failed(results) & set(CONTRACT_METRICS) == set()
        assert _hints(results) == []
    assert _failed(_score(NVDA, en_pair)) & set(CONTRACT_METRICS) == set()
    assert (
        "label in de in label position" in _score(NVDA, en_foreign)["language"].reason
    )


def test_numbers_match_fails_on_an_invented_number():
    text = NVDA_TEXT.replace(
        "which is above the last close.",
        "which is above the last close. Volume was 1.2 million shares.",
    )
    results = _score(NVDA, text)

    assert results["numbers_match"].reason == "numbers not in the data: 1.2"


def test_coverage_fails_when_part_1_omits_a_cited_value():
    text = NVDA_TEXT.replace(
        " and ends at 226.34 after 30 trading days", " over 30 trading days"
    )
    results = _score(NVDA, text)

    assert results["coverage"].reason == "part 1 does not cite: forecast end"
    assert results["numbers_match"].passed is True


def test_coverage_reads_a_range_written_with_a_dash():
    text = NVDA_TEXT.replace(
        "The forecast starts at 221.78 and ends at 226.34 after 30 trading days",
        "The forecast runs 221.78–226.34 over 30 trading days",
    )
    results = _score(NVDA, text)

    assert results["coverage"].passed is True
    assert results["numbers_match"].passed is True


def test_coverage_reads_german_and_grouped_numbers():
    case = CASES["de-dax-30-thousands"]
    text = SAP_TEXT.replace(
        "SAP.DE schloss zuletzt bei 201,55. Die Prognose startet bei 201,44 und endet nach 5 Handelstagen bei 201,40.",
        "Der DAX schloss zuletzt bei 18.234,56. Die Prognose startet bei 18.260,10 und endet nach 30 Handelstagen bei 18.712,45.",
    )

    assert _score(case, text)["coverage"].passed is True


def test_length_counts_labels_and_disclaimer_and_fails_above_the_limit():
    filler = " ".join(["word"] * 40)
    text = NVDA_TEXT.replace(
        "not as a prediction of the outcome.",
        "not as a prediction of the outcome. " + filler,
    )
    results = _score(NVDA, text)

    assert results["length"].reason == "161 words, limit 150"
    assert results["length"].info["words_without_labels_and_disclaimer"] == 161 - 8 - 5


def test_complete_fails_on_a_truncated_reply():
    results = _score(NVDA, NVDA_TEXT[:200], stop_reason="max_tokens")

    assert results["complete"].reason == "stop_reason max_tokens"
    assert "structure" in _failed(results)


def test_a_recorded_error_fails_every_metric_with_the_error():
    results = score.score(NVDA, None, None, error="CommentaryError: refusal")

    assert results["complete"].reason == "no reply: CommentaryError: refusal"
    assert _failed(results) == set(METRICS)


# --- communication metrics ------------------------------------------------------

CLEAN_EN = """**Price movement**
NVDA last closed at 221.97. The forecast starts at 221.78 and ends at 226.34 after 30 trading days, above the last close.

**Model quality**
Over a 30-day backtest horizon the model's MAE was 6.1651 against 6.4573 for the naive baseline, with a MAPE of 2.77%. The model beats the naive baseline by a narrow margin.

**Interpretation**
The forecast is a statistical projection from past prices and does not account for news or market shocks. A narrow backtest edge does not guarantee future accuracy.

Educational analysis — not investment advice."""


def test_clean_prose_passes_every_metric():
    results = _score(NVDA, CLEAN_EN)

    assert _failed(results) == set()


def test_jargon_free_flags_identifiers_and_python_literals_but_not_vocabulary():
    with_code = CLEAN_EN.replace(
        "The model beats the naive baseline by a narrow margin.",
        "mae_model is below mae_naive, so beats_naive is True.",
    )
    results = _score(NVDA, with_code)

    assert results["jargon_free"].reason == (
        "code in the text: True, beats_naive, mae_model, mae_naive"
    )
    assert _score(NVDA, CLEAN_EN)["jargon_free"].passed is True  # MAE, MAPE, backtest


@pytest.mark.parametrize(
    "token, locale",
    [
        ("18.234,56", "de"),
        ("18,234.56", "en"),
        ("6,88", "de"),
        ("6.88", "en"),
        ("2.7679", "en"),
        ("2,7679", "de"),
        ("1.234.567", "de"),
        ("1,234,567", "en"),
        ("1.500", None),
        ("41,828", None),
    ],
)
def test_number_locale(token, locale):
    assert score._number_locale(token) == locale


def test_locale_numbers_fails_on_the_other_format_and_ignores_ambiguous_tokens():
    german_with_english_decimals = SAP_TEXT.replace("201,55", "201.55")
    results = _score(SAP_CASE, german_with_english_decimals)

    assert results["locale_numbers"].reason == "not de number format: 201.55"
    assert _score(SAP_CASE, SAP_TEXT)["locale_numbers"].passed is True
    english_with_a_comma = CLEAN_EN.replace("2.77%", "2,77%")
    assert _score(NVDA, english_with_a_comma)["locale_numbers"].reason == (
        "not en number format: 2,77"
    )
    ambiguous = CLEAN_EN.replace("with a MAPE of 2.77%", "roughly 1,500 shares")
    assert _score(NVDA, ambiguous)["locale_numbers"].passed is True


def test_no_title_line_fails_on_any_line_before_part_1():
    results = _score(NVDA, "NVDA — 30-day commentary\n\n" + CLEAN_EN)

    assert (
        results["no_title_line"].reason
        == "line before part 1: 'NVDA - 30-day commentary'"
    )
    assert results["structure"].passed is True  # tolerated there, with a hint


def test_label_format_requires_bold_own_line_exact_name_no_number():
    numbered = CLEAN_EN.replace("**Price movement**", "**1. Price movement**")
    colon = CLEAN_EN.replace("**Model quality**", "**Model quality:**")
    inline = CLEAN_EN.replace(
        "**Interpretation**\nThe forecast", "**Interpretation:** The forecast"
    )
    plain = CLEAN_EN.replace("**Price movement**", "Price movement")
    synonym = CLEAN_EN.replace("**Interpretation**", "**Limitations**")

    assert _score(NVDA, numbered)["label_format"].reason == (
        "expected **Price movement**; written as '**1. Price movement**'"
    )
    assert _score(NVDA, colon)["label_format"].reason == (
        "expected **Model quality**; written as '**Model quality:**'"
    )
    assert _score(NVDA, inline)["label_format"].reason.startswith(
        "expected **Interpretation**; written as '**Interpretation:** The forecast"
    )
    assert _score(NVDA, plain)["label_format"].reason == (
        "expected **Price movement**; written as 'Price movement'"
    )
    assert _score(NVDA, synonym)["label_format"].reason == (
        "expected **Interpretation**; written as '**Limitations**'"
    )
    assert _score(NVDA, synonym)["structure"].passed is True  # the contract allows it


# --- the recorded baseline and the report -------------------------------------


def test_recorded_baseline_scores_and_the_report_builds():
    versions = run.recorded_versions()
    assert versions, "no recorded baseline under evals/recorded/"
    all_cases = set(CASES) | set(HOLDOUT)

    for version in versions:
        samples = run.load_samples(version)
        markdown, summary = run.build_report(version)

        assert {s["case_id"] for s in samples} == all_cases
        assert Counter(s["case_id"] for s in samples) == {
            cid: run.SAMPLES for cid in all_cases
        }
        assert all(s["prompt_version"] == version for s in samples)
        assert f"# Commentary eval — prompt {version}" in markdown
        assert "**Scoring rule.**" in markdown
        assert "## Golden set" in markdown and "## Hold-out set" in markdown
        for metric in METRICS:
            assert f"| {metric} |" in markdown
            assert summary["sets"]["golden"][metric]["cases"] == len(CASES)
            assert summary["sets"]["holdout"][metric]["cases"] == len(HOLDOUT)
        assert "## Scorer changes" in markdown
        assert "## Spec gaps" in markdown and "- **G12**" in markdown
        assert run.render_table(summary).startswith("[golden]")


def test_regression_gate_correctness_never_falls_between_prompt_versions():
    """The newest recorded prompt version must not lose a case on any
    correctness metric against the previous one. Communication metrics are
    reported, not gated. Never adjust the scorer to make this green."""
    versions = run.recorded_versions()
    if len(versions) < 2:
        pytest.skip("one prompt version recorded; nothing to compare")
    previous, newest = versions[-2], versions[-1]
    before = run.build_summary(previous)[2]
    after = run.build_summary(newest)[2]

    for metric in CORRECTNESS_METRICS:
        assert run.case_pass_rate(after, metric) >= run.case_pass_rate(
            before, metric
        ), f"{metric}: {newest} passes fewer cases than {previous}"


def test_version_keys_sort_by_date_then_counter():
    assert sorted(
        ["2026-10-23.10", "2026-10-23.2", "2026-10-22.9"], key=run.version_key
    ) == [
        "2026-10-22.9",
        "2026-10-23.2",
        "2026-10-23.10",
    ]


def test_committed_report_matches_the_recorded_samples():
    for version in run.recorded_versions():
        path = run.RESULTS_DIR / f"{version}.md"
        assert path.exists(), f"run `python -m evals.run` and commit {path.name}"
        markdown, _summary = run.build_report(version)
        assert path.read_text(encoding="utf-8") == markdown


def test_record_refuses_without_confirmation_and_in_ci(monkeypatch, capsys):
    cases = run.load_cases("en-nvda-30-beats")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: True)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    with pytest.raises(SystemExit, match="--yes"):
        run.record(cases, samples=3, yes=False)
    assert "1 cases (golden) × 3 samples = 3 calls" in capsys.readouterr().out

    monkeypatch.setenv("CI", "true")
    with pytest.raises(SystemExit, match="never runs in CI"):
        run.record(cases, samples=3, yes=True)


def test_case_files_are_valid_json_with_the_documented_fields():
    for path in sorted(p for d in run.SETS.values() for p in d.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        assert path.stem == case["id"]
        assert set(case) == {
            "id",
            "ticker",
            "language",
            "horizon",
            "last_close",
            "forecast",
            "metrics",
            "expect",
            "covers",
        }
