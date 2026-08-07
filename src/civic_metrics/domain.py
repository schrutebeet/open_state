from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str
    frequency: str


@dataclass(frozen=True)
class ObservationCandidate:
    indicator_code: str
    source_code: str
    dataset_code: str
    period: Period
    value: Decimal
    unit: str
    geography: str = "ES"
    status: str = "published"
    is_provisional: bool = False
    source_series: str | None = None
    source_url: str | None = None
    published_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class DatasetPayload:
    dataset_code: str
    source_code: str
    fetched_at: datetime
    source_url: str
    content_type: str
    body: bytes
    sha256: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedSeries:
    name: str
    points: tuple[tuple[Period, Decimal], ...]
    metadata: dict[str, Any] = field(default_factory=dict)
