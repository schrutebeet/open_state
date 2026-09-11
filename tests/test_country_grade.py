from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from civic_metrics.catalog import load_catalog, sync_catalog
from civic_metrics.db import create_database_engine, init_database, make_session_factory
from civic_metrics.models import CountryGrade, Indicator, Observation
from civic_metrics.processors.country_grade import (
    component_score,
    materialise_country_grade,
    month_age,
    previous_month,
)


def test_previous_month_is_a_complete_calendar_month() -> None:
    assert previous_month(date(2026, 9, 10)) == (date(2026, 8, 1), date(2026, 8, 31))
    assert previous_month(date(2026, 1, 1)) == (date(2025, 12, 1), date(2025, 12, 31))
    assert month_age(date(2026, 6, 30), date(2026, 8, 31)) == 2


def test_component_scores_are_bounded() -> None:
    assert component_score("cpi_yoy", Decimal("2")) == Decimal("100")
    assert component_score("unemployment_rate", Decimal("30")) == Decimal("0")
    assert component_score("gdp_real_qoq", Decimal("10")) == Decimal("100")


def test_materialises_one_reproducible_monthly_grade() -> None:
    root = Path(__file__).resolve().parents[1]
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    init_database(engine)
    factory = make_session_factory(engine)
    values = {
        "gdp_real_qoq": (Decimal("0.5"), date(2026, 6, 30)),
        "unemployment_rate": (Decimal("10"), date(2026, 6, 30)),
        "youth_unemployment_rate": (Decimal("22"), date(2026, 6, 30)),
        "cpi_yoy": (Decimal("2"), date(2026, 8, 31)),
        "registered_unemployment": (Decimal("2500000"), date(2026, 8, 31)),
        "affiliates_per_pensioner": (Decimal("2.5"), date(2026, 8, 31)),
    }
    with factory.begin() as session:
        sync_catalog(session, load_catalog(root / "config"))
        for code, (value, end) in values.items():
            indicator = session.scalar(select(Indicator).where(Indicator.code == code))
            assert indicator is not None
            session.add(
                Observation(
                    indicator_id=indicator.id,
                    raw_artifact_id=None,
                    source_code="test",
                    dataset_code="test",
                    period_start=end.replace(day=1),
                    period_end=end,
                    period_label=end.strftime("%Y-%m"),
                    frequency=indicator.frequency,
                    geography="ES",
                    value=value,
                    unit=indicator.unit,
                    status="published",
                    is_provisional=False,
                    source_series=None,
                    source_url="https://example.test",
                    published_at=None,
                    retrieved_at=datetime.now(UTC),
                    metadata_json={},
                )
            )
        session.flush()
        first = materialise_country_grade(session, today=date(2026, 9, 10))
        second = materialise_country_grade(session, today=date(2026, 9, 10))
        grade = session.scalar(select(CountryGrade))
        assert grade is not None
        assert (grade.period_start, grade.period_end) == (date(2026, 8, 1), date(2026, 8, 31))
        assert grade.status == "complete"
        assert grade.coverage == Decimal("1")
        assert first["inserted"] is True
        assert second["inserted"] is False
        assert len(grade.inputs_json["active_components"]) == 6


def test_monthly_component_falls_back_to_previous_month() -> None:
    root = Path(__file__).resolve().parents[1]
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    init_database(engine)
    factory = make_session_factory(engine)
    with factory.begin() as session:
        sync_catalog(session, load_catalog(root / "config"))
        indicator = session.scalar(select(Indicator).where(Indicator.code == "cpi_yoy"))
        assert indicator is not None
        session.add(
            Observation(
                indicator_id=indicator.id,
                source_code="test",
                dataset_code="test",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
                period_label="2026-07",
                frequency=indicator.frequency,
                geography="ES",
                value=Decimal("2"),
                unit=indicator.unit,
                status="published",
                is_provisional=False,
                retrieved_at=datetime.now(UTC),
                metadata_json={},
            )
        )
        session.flush()
        result = materialise_country_grade(session, today=date(2026, 9, 10))
        grade = session.scalar(select(CountryGrade))
        assert grade is not None
        active = grade.inputs_json["active_components"]
        cpi = next(item for item in active if item["indicator_code"] == "cpi_yoy")
        assert result["status"] == "partial"
        assert cpi["selection"] == "previous_month"
        assert cpi["source_period_end"] == "2026-07-31"
