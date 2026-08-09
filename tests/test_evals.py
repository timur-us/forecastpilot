"""Offline tests for app.evals.check_numbers — no network, no API keys."""

from app.evals import check_numbers

PAYLOAD = {
    "last_close": 182.34,
    "forecast": [1200.0, 1350.5],
    "metrics": {
        "mae_model": 45.6789,
        "beats_naive": True,
        "backtest_horizon": 30,
    },
    "horizon_days": 30,
}
# Derived: delta = 1350.5 - 1200.0 = 150.5
#          pct change first -> last = 150.5 / 1200.0 * 100 ~= 12.5417


def test_de_formats_delta_and_invented_number():
    text = (
        "Kursbewegung: Der Schlusskurs liegt bei 182,34 EUR. "
        "Die Prognose bewegt sich von 1.200,00 auf 1.350,50 (+150,50, +12,54%). "
        "Modellgüte: MAE 45,68, Backtest-Horizont 30 Tage, beats_naive. "
        "Einschränkung: Ziel 999,99 ist erfunden. "
        "Bildungsanalyse — keine Anlageberatung."
    )

    result = check_numbers(text, PAYLOAD)

    assert result["numbers_checked"] == 8
    assert result["numbers_matched"] == 7
    assert result["unmatched"] == ["999,99"]
    assert result["passed"] is False


def test_en_thousands_separator_and_percent():
    text = "Price moved from $1,200.00 to $1,350.50, a change of +12.54%."

    result = check_numbers(text, PAYLOAD)

    assert result["unmatched"] == []
    assert result["passed"] is True
    assert result["numbers_checked"] == result["numbers_matched"] == 3


def test_all_numbers_grounded_passes_clean():
    text = "Last close 182.34. Forecast 1200.00 to 1350.50 over 30 days."

    result = check_numbers(text, PAYLOAD)

    assert result["passed"] is True
    assert result["unmatched"] == []


def test_list_markers_from_system_prompt_structure_are_ignored():
    text = (
        "1. Kursbewegung: 182.34.\n"
        "2. Modellgüte: beats naive baseline.\n"
        "3. Einschränkung: not investment advice."
    )

    result = check_numbers(text, PAYLOAD)

    assert result["unmatched"] == []
    assert result["numbers_checked"] == 1
