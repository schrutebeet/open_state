from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser

from civic_metrics.domain import Period

SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "set": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}
SPAIN_TIMEZONE = ZoneInfo("Europe/Madrid")


def normalise_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def parse_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"Cannot parse non-finite numeric value from {value!r}")
        return value
    if isinstance(value, (int, float)):
        parsed = Decimal(str(value))
        if not parsed.is_finite():
            raise ValueError(f"Cannot parse non-finite numeric value from {value!r}")
        return parsed

    text = str(value).strip().replace("\u00a0", " ")
    if not text:
        raise ValueError(f"Cannot parse numeric value from {value!r}")

    sign = ""
    if text[:1] in {"+", "-"}:
        sign, text = text[0], text[1:].strip()
    if text[:1] in {"+", "-"} or not text:
        raise ValueError(f"Invalid numeric value {value!r}")

    if any(char not in "0123456789., " for char in text):
        raise ValueError(f"Invalid numeric value {value!r}")

    if " " in text:
        space_parts = text.split(" ")
        if any(not part for part in space_parts):
            raise ValueError(f"Invalid numeric grouping in {value!r}")
        text = "".join(space_parts)

    separators = {separator for separator in ",." if separator in text}
    if not separators:
        normalised = text
    elif len(separators) == 2:
        decimal_separator = "," if text.rfind(",") > text.rfind(".") else "."
        grouping_separator = "." if decimal_separator == "," else ","
        integer_part, fractional_part = text.rsplit(decimal_separator, 1)
        if not fractional_part.isdigit() or grouping_separator in fractional_part:
            raise ValueError(f"Invalid decimal value {value!r}")
        if not _valid_grouped_integer(integer_part, grouping_separator):
            raise ValueError(f"Invalid numeric grouping in {value!r}")
        normalised = integer_part.replace(grouping_separator, "") + "." + fractional_part
    else:
        separator = next(iter(separators))
        parts = text.split(separator)
        if len(parts) == 2:
            if len(parts[1]) == 3:
                raise ValueError(f"Ambiguous numeric value {value!r}")
            if not parts[0].isdigit() or not parts[1].isdigit():
                raise ValueError(f"Invalid decimal value {value!r}")
            normalised = ".".join(parts)
        elif _valid_grouped_integer(text, separator):
            normalised = text.replace(separator, "")
        else:
            raise ValueError(f"Invalid numeric grouping in {value!r}")

    try:
        return Decimal(sign + normalised)
    except InvalidOperation as exc:
        raise ValueError(f"Cannot parse numeric value from {value!r}") from exc


def _valid_grouped_integer(text: str, separator: str) -> bool:
    parts = text.split(separator)
    return (
        len(parts) > 1
        and 1 <= len(parts[0]) <= 3
        and parts[0].isdigit()
        and all(len(part) == 3 and part.isdigit() for part in parts[1:])
    )


def period_from_datetime(
    value: datetime,
    frequency: str,
    *,
    calendar_timezone: tzinfo = UTC,
) -> Period:
    value = value.astimezone(calendar_timezone).date()
    if frequency == "monthly":
        start = value.replace(day=1)
        end = value.replace(day=calendar.monthrange(value.year, value.month)[1])
        return Period(start, end, f"{value.year}-{value.month:02d}", frequency)
    if frequency == "quarterly":
        quarter = (value.month - 1) // 3 + 1
        start_month = 3 * (quarter - 1) + 1
        start = date(value.year, start_month, 1)
        end_month = start_month + 2
        end = date(value.year, end_month, calendar.monthrange(value.year, end_month)[1])
        return Period(start, end, f"{value.year}-Q{quarter}", frequency)
    if frequency == "annual":
        return Period(date(value.year, 1, 1), date(value.year, 12, 31), str(value.year), frequency)
    return Period(value, value, value.isoformat(), frequency)


def period_from_label(label: str, default_frequency: str) -> Period:
    text = normalise_text(label)

    compact_month = re.search(r"\b(20\d{2})[-_/]?m?((?:0?[1-9])|(?:1[0-2]))\b", text)
    if compact_month and default_frequency == "monthly":
        year = int(compact_month.group(1))
        month = int(compact_month.group(2))
        return period_from_datetime(datetime(year, month, 1, tzinfo=UTC), "monthly")

    # Common Spanish quarterly labels: 2T 2026, T2 2026, 2026T2, Q2 2026.
    quarter_patterns = (
        r"\b([1-4])\s*(?:t|q)\D*(20\d{2})\b",
        r"\b(?:q|t|trimestre)\s*([1-4])\D*(20\d{2})\b",
        r"\b(20\d{2})\D*(?:q|t)\s*([1-4])\b",
    )
    for index, pattern in enumerate(quarter_patterns):
        match = re.search(pattern, text)
        if not match:
            continue
        if index < 2:
            quarter, year = int(match.group(1)), int(match.group(2))
        else:
            year, quarter = int(match.group(1)), int(match.group(2))
        month = (quarter - 1) * 3 + 1
        return period_from_datetime(datetime(year, month, 1, tzinfo=UTC), "quarterly")

    year_match = re.search(r"\b(?:19|20)\d{2}\b", text)
    month_number: int | None = None
    for month_name, number in SPANISH_MONTHS.items():
        if re.search(rf"\b{re.escape(month_name)}\b", text):
            month_number = number
            break
    if year_match and month_number:
        return period_from_datetime(
            datetime(int(year_match.group(0)), month_number, 1, tzinfo=UTC),
            "monthly",
        )
    if year_match and default_frequency == "annual":
        return period_from_datetime(
            datetime(int(year_match.group(0)), 1, 1, tzinfo=UTC),
            "annual",
        )

    # Avoid dateutil interpreting a pure heading such as "Trimestre" as a date.
    if not re.search(r"\d", text):
        raise ValueError(f"Could not parse period label {label!r}")
    try:
        parsed = date_parser.parse(label, dayfirst=True, fuzzy=True)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return period_from_datetime(parsed, default_frequency)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"Could not parse period label {label!r}") from exc


def period_from_ine_date(value: Any, frequency: str, year: int | None = None) -> Period:
    if isinstance(value, (int, float)):
        milliseconds = float(value)
        if milliseconds > 10_000_000_000:
            parsed = datetime.fromtimestamp(milliseconds / 1000, tz=SPAIN_TIMEZONE)
        else:
            parsed = datetime.fromtimestamp(milliseconds, tz=SPAIN_TIMEZONE)
        return period_from_datetime(
            parsed,
            frequency,
            calendar_timezone=SPAIN_TIMEZONE,
        )
    if value:
        parsed = date_parser.parse(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SPAIN_TIMEZONE)
        return period_from_datetime(
            parsed,
            frequency,
            calendar_timezone=SPAIN_TIMEZONE,
        )
    if year:
        return period_from_datetime(
            datetime(year, 1, 1, tzinfo=SPAIN_TIMEZONE),
            frequency,
            calendar_timezone=SPAIN_TIMEZONE,
        )
    raise ValueError("INE observation does not contain a usable date")


def prior_period(period: Period) -> Period:
    if period.frequency == "monthly":
        end_previous = period.start - timedelta(days=1)
        return period_from_datetime(
            datetime(end_previous.year, end_previous.month, 1, tzinfo=UTC),
            "monthly",
        )
    if period.frequency == "quarterly":
        end_previous = period.start - timedelta(days=1)
        return period_from_datetime(
            datetime(end_previous.year, end_previous.month, 1, tzinfo=UTC),
            "quarterly",
        )
    return period_from_datetime(
        datetime(period.start.year - 1, 1, 1, tzinfo=UTC), "annual"
    )
