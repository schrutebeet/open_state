from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

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


def normalise_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def parse_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().replace("\u00a0", " ")
    text = re.sub(r"[^0-9,.-]", "", text)
    if not text:
        raise ValueError(f"Cannot parse numeric value from {value!r}")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        tail = text.rsplit(",", 1)[-1]
        text = text.replace(".", "")
        text = text.replace(",", "." if len(tail) <= 3 else "")
    else:
        parts = text.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3):
            text = "".join(parts)
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Cannot parse numeric value from {value!r}") from exc


def period_from_datetime(value: datetime, frequency: str) -> Period:
    value = value.astimezone(timezone.utc).date()
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
        return period_from_datetime(datetime(year, month, 1, tzinfo=timezone.utc), "monthly")

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
        return period_from_datetime(datetime(year, month, 1, tzinfo=timezone.utc), "quarterly")

    year_match = re.search(r"\b(?:19|20)\d{2}\b", text)
    month_number: int | None = None
    for month_name, number in SPANISH_MONTHS.items():
        if re.search(rf"\b{re.escape(month_name)}\b", text):
            month_number = number
            break
    if year_match and month_number:
        return period_from_datetime(
            datetime(int(year_match.group(0)), month_number, 1, tzinfo=timezone.utc),
            "monthly",
        )
    if year_match and default_frequency == "annual":
        return period_from_datetime(
            datetime(int(year_match.group(0)), 1, 1, tzinfo=timezone.utc),
            "annual",
        )

    # Avoid dateutil interpreting a pure heading such as "Trimestre" as a date.
    if not re.search(r"\d", text):
        raise ValueError(f"Could not parse period label {label!r}")
    try:
        parsed = date_parser.parse(label, dayfirst=True, fuzzy=True)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return period_from_datetime(parsed, default_frequency)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"Could not parse period label {label!r}") from exc


def period_from_ine_date(value: Any, frequency: str, year: int | None = None) -> Period:
    if isinstance(value, (int, float)):
        milliseconds = float(value)
        if milliseconds > 10_000_000_000:
            parsed = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
        else:
            parsed = datetime.fromtimestamp(milliseconds, tz=timezone.utc)
        return period_from_datetime(parsed, frequency)
    if value:
        parsed = date_parser.parse(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return period_from_datetime(parsed, frequency)
    if year:
        return period_from_datetime(datetime(year, 1, 1, tzinfo=timezone.utc), frequency)
    raise ValueError("INE observation does not contain a usable date")


def prior_period(period: Period) -> Period:
    if period.frequency == "monthly":
        end_previous = period.start - timedelta(days=1)
        return period_from_datetime(
            datetime(end_previous.year, end_previous.month, 1, tzinfo=timezone.utc),
            "monthly",
        )
    if period.frequency == "quarterly":
        end_previous = period.start - timedelta(days=1)
        return period_from_datetime(
            datetime(end_previous.year, end_previous.month, 1, tzinfo=timezone.utc),
            "quarterly",
        )
    return period_from_datetime(
        datetime(period.start.year - 1, 1, 1, tzinfo=timezone.utc), "annual"
    )
