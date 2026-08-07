from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from civic_metrics.catalog import load_catalog, sync_catalog
from civic_metrics.db import create_database_engine, init_database, make_session_factory
from civic_metrics.domain import ObservationCandidate, Period
from civic_metrics.models import Indicator, Observation, ObservationDependency
from civic_metrics.processors import DerivedIndicatorEngine
from civic_metrics.repository import save_observation


def _period(month: int) -> Period:
    return Period(date(2026, month, 1), date(2026, month, 28), f"2026-{month:02d}", "monthly")


def test_derived_indicator_reuses_stored_observations() -> None:
    catalog = load_catalog(Path("config"))
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    init_database(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        sync_catalog(session, catalog)
        save_observation(
            session,
            ObservationCandidate(
                indicator_code="goods_exports",
                source_code="datacomex",
                dataset_code="datacomex_total_trade",
                period=_period(1),
                value=Decimal("300"),
                unit="eur",
                source_series="exports",
            ),
            None,
        )
        save_observation(
            session,
            ObservationCandidate(
                indicator_code="goods_imports",
                source_code="datacomex",
                dataset_code="datacomex_total_trade",
                period=_period(1),
                value=Decimal("250"),
                unit="eur",
                source_series="imports",
            ),
            None,
        )
        definition = catalog.indicator_by_code["goods_trade_balance"]
        observation = DerivedIndicatorEngine(session).materialise(definition)
        session.commit()
        assert observation is not None
        assert Decimal(str(observation.value)) == Decimal("50")
        dependencies = session.scalars(
            select(ObservationDependency).where(
                ObservationDependency.observation_id == observation.id
            )
        ).all()
        assert len(dependencies) == 2
        assert session.scalar(
            select(Indicator.code).join(Observation).where(Observation.id == observation.id)
        ) == "goods_trade_balance"
