from decimal import Decimal

from civic_metrics.connectors.datacomex import _parse_datacomex_euros


def test_datacomex_euros_accepts_three_decimal_places_with_comma() -> None:
    assert _parse_datacomex_euros("31139899583,891") == Decimal("31139899583.891")


def test_datacomex_euros_treats_a_lone_comma_as_the_api_decimal_mark() -> None:
    assert _parse_datacomex_euros("1,234") == Decimal("1.234")
