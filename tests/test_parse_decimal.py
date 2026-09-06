from decimal import Decimal

import pytest

from civic_metrics.parsers.common import parse_decimal


def test_comma_with_four_fractional_digits_is_decimal() -> None:
    assert parse_decimal("21389218594,3171") == Decimal("21389218594.3171")


def test_comma_separated_thousands_are_grouped() -> None:
    assert parse_decimal("21,389,218,594") == Decimal("21389218594")


def test_single_three_digit_comma_is_decimal_by_default() -> None:
    with pytest.raises(ValueError, match="Ambiguous"):
        parse_decimal("1,234")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.234,56", Decimal("1234.56")),
        ("1,234.56", Decimal("1234.56")),
        ("1 234,56", Decimal("1234.56")),
        ("-21,389,218,594", Decimal("-21389218594")),
    ],
)
def test_valid_locale_formats(raw: str, expected: Decimal) -> None:
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize("raw", ["12.34.567", "1,23,456", "12abc34", "1,234,56", "123,"])
def test_invalid_or_ambiguous_formats_hard_fail(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_decimal(raw)