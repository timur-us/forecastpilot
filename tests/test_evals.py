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


def test_de_decimals_with_four_plus_places_are_not_split():
    # Regression: "18,6936" / "13,6393" used to be cut after 3 digits,
    # stranding "6" / "3" as bogus extra tokens.
    payload = {
        "last_close": 18.6936,
        "forecast": [13.6393, 20.0],
        "metrics": {},
        "horizon_days": 30,
    }
    text = "Werte: 18,6936 und 13,6393."

    result = check_numbers(text, payload)

    assert result["numbers_checked"] == 2
    assert result["unmatched"] == []
    assert result["passed"] is True


def test_list_markers_from_system_prompt_structure_are_ignored():
    text = (
        "1. Kursbewegung: 182.34.\n"
        "2. Modellgüte: beats naive baseline.\n"
        "3. Einschränkung: not investment advice."
    )

    result = check_numbers(text, PAYLOAD)

    assert result["unmatched"] == []
    assert result["numbers_checked"] == 1


def test_markdown_bold_numbered_headings_are_not_extracted_as_values():
    # Regression: German commentary renders the fixed 3-part structure as
    # bold markdown headings ("**1. Kursbewegung**"), which the old
    # line-start-only marker regex didn't recognize (it required plain
    # whitespace before the digit, not "**"), so the ordinals 1/2/3 leaked
    # through as unmatched "invented" numbers.
    payload = {
        "last_close": 182.34,
        "forecast": [1200.0, 1350.5],
        "metrics": {"mae_naive": 41.828, "beats_naive": True, "backtest_horizon": 30},
        "horizon_days": 30,
    }
    text = (
        "**1. Kursbewegung**\n"
        "Der Schlusskurs liegt bei 182,34 EUR, die Prognose reicht bis "
        "1.350,50 EUR.\n"
        "**2. Modellgüte**\n"
        "Die mittlere Abweichung des naiven Modells liegt bei 41,828 EUR "
        "über 30 Tage.\n"
        "**3. Einschränkung**\n"
        "Bildungsanalyse — keine Anlageberatung."
    )

    result = check_numbers(text, payload)

    assert "1" not in result["unmatched"]
    assert "2" not in result["unmatched"]
    assert "3" not in result["unmatched"]
    assert result["numbers_checked"] == 4  # headings excluded entirely
    assert result["unmatched"] == []
    assert result["passed"] is True


def test_ambiguous_de_comma_number_matches_via_decimal_reading():
    # "41,828" could be thousands (41828) or a 3-decimal fraction
    # (41.828); the payload only has the decimal reading, so it must
    # still count as matched, not unmatched.
    payload = {
        "last_close": 0,
        "forecast": [],
        "metrics": {"mae_naive": 41.828},
        "horizon_days": 0,
    }

    result = check_numbers("MAE 41,828 EUR.", payload)

    assert "41,828" not in result["unmatched"]
    assert result["numbers_matched"] == 1
    assert result["passed"] is True


def test_ambiguous_dot_number_matches_via_thousands_reading():
    # Same ambiguity, mirrored: "41.828" (with a point) against a payload
    # value of 41828 proves the thousands reading is also generated, not
    # just the decimal one.
    payload = {
        "last_close": 0,
        "forecast": [],
        "metrics": {"volume": 41828},
        "horizon_days": 0,
    }

    result = check_numbers("Volumen: 41.828 Stück.", payload)

    assert "41.828" not in result["unmatched"]
    assert result["numbers_matched"] == 1
    assert result["passed"] is True


def test_invented_ambiguous_number_still_fails_both_readings():
    # The guard must keep catching real hallucinations: an ambiguous-shaped
    # invented number ("77,777") matches neither its thousands (77777) nor
    # its decimal (77.777) reading against this payload, so it must still
    # land in unmatched.
    text = "Fantasiewert: 77,777 EUR."

    result = check_numbers(text, PAYLOAD)

    assert result["unmatched"] == ["77,777"]
    assert result["passed"] is False
