"""Generate a monthly, point-in-time country conditions grade from official data."""

from __future__ import annotations

import calendar
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from civic_metrics.models import CountryGrade, Indicator, Observation

GRADE_CODE = "country_conditions"
METHODOLOGY_VERSION = "country-conditions-v1"
D = Decimal


@dataclass(frozen=True)
class Component:
    code: str
    name: str
    dimension: str
    weight: Decimal
    max_age_months: int
    formula: str


# Fixed editorial anchors. They are transparent policy choices, not scientific thresholds.
COMPONENTS = (
    Component(
        "gdp_real_qoq",
        "Crecimiento trimestral del PIB real",
        "actividad",
        D("0.25"),
        3,
        "clip(50 + 20 × PIB real trimestral)",
    ),
    Component(
        "unemployment_rate", "Tasa de paro", "empleo", D("0.20"), 3, "clip(4 × (25 − tasa de paro))"
    ),
    Component(
        "youth_unemployment_rate",
        "Tasa de paro juvenil (20–24)",
        "empleo",
        D("0.10"),
        3,
        "clip(2 × (50 − tasa de paro juvenil))",
    ),
    Component(
        "cpi_yoy",
        "Inflación general",
        "precios",
        D("0.15"),
        1,
        "clip(100 − 12,5 × abs(inflación − 2))",
    ),
    Component(
        "registered_unemployment",
        "Personas inscritas como desempleadas",
        "empleo",
        D("0.10"),
        1,
        "clip(100 × (4.000.000 − paro registrado) / 2.000.000)",
    ),
    Component(
        "affiliates_per_pensioner",
        "Afiliaciones por pensionista",
        "pensiones",
        D("0.20"),
        1,
        "clip(50 × (afiliaciones por pensionista − 1))",
    ),
)

DISABLED_DIMENSIONS = {
    "finanzas_publicas": "El dato fiscal agregado más útil disponible tiene un retraso excesivo.",
    "salud": "No hay indicadores de salud en el catálogo actual.",
    "educacion": "No hay indicadores de educación en el catálogo actual.",
    "vivienda": "No hay indicadores de vivienda en el catálogo actual.",
    "medioambiente": "No hay indicadores medioambientales en el catálogo actual.",
}


def previous_month(value: date | None = None) -> tuple[date, date]:
    """Return the first and final day of the calendar month preceding *value*."""
    value = value or datetime.now(UTC).date()
    year, month = (value.year - 1, 12) if value.month == 1 else (value.year, value.month - 1)
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def month_age(source_end: date, reference_end: date) -> int:
    return (reference_end.year - source_end.year) * 12 + reference_end.month - source_end.month


def clip(value: Decimal) -> Decimal:
    return max(D("0"), min(D("100"), value))


def component_score(code: str, value: Decimal) -> Decimal:
    if code == "gdp_real_qoq":
        return clip(D("50") + D("20") * value)
    if code == "unemployment_rate":
        return clip(D("4") * (D("25") - value))
    if code == "youth_unemployment_rate":
        return clip(D("2") * (D("50") - value))
    if code == "cpi_yoy":
        return clip(D("100") - D("12.5") * abs(value - D("2")))
    if code == "registered_unemployment":
        return clip(D("100") * (D("4000000") - value) / D("2000000"))
    if code == "affiliates_per_pensioner":
        return clip(D("50") * (value - D("1")))
    raise ValueError(f"Unsupported country-grade component: {code}")


def _latest_observation(session: Session, code: str, reference_end: date) -> Observation | None:
    return session.scalar(
        select(Observation)
        .join(Indicator)
        .where(
            Indicator.code == code,
            Indicator.enabled.is_(True),
            Observation.status == "published",
            Observation.geography == "ES",
            Observation.period_end <= reference_end,
        )
        .order_by(
            Observation.period_end.desc(), Observation.retrieved_at.desc(), Observation.id.desc()
        )
        .limit(1)
    )


def _month_observation(
    session: Session, code: str, period_start: date, period_end: date
) -> Observation | None:
    return session.scalar(
        select(Observation)
        .join(Indicator)
        .where(
            Indicator.code == code,
            Indicator.enabled.is_(True),
            Observation.status == "published",
            Observation.geography == "ES",
            Observation.period_start == period_start,
            Observation.period_end == period_end,
        )
        .order_by(Observation.retrieved_at.desc(), Observation.id.desc())
        .limit(1)
    )


def materialise_country_grade(
    session: Session,
    today: date | None = None,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
    replace_existing: bool = False,
) -> dict[str, object]:
    """Store one grade for a calendar month.

    If no explicit period is supplied, the month immediately preceding *today*
    is used. ``replace_existing`` removes the existing grade for that month.
    """
    if (period_start is None) != (period_end is None):
        raise ValueError("period_start and period_end must be supplied together")
    if period_start is None or period_end is None:
        period_start, period_end = previous_month(today)
    if replace_existing:
        session.execute(
            delete(CountryGrade).where(
                CountryGrade.grade_code == GRADE_CODE,
                CountryGrade.geography == "ES",
                CountryGrade.period_start == period_start,
                CountryGrade.period_end == period_end,
            )
        )
    active: list[dict[str, object]] = []
    unavailable: list[dict[str, object]] = []
    for component in COMPONENTS:
        observation = _month_observation(session, component.code, period_start, period_end)
        selection = "target_month"
        if observation is None:
            fallback_start, fallback_end = previous_month(period_start)
            observation = _month_observation(session, component.code, fallback_start, fallback_end)
            if observation is not None:
                selection = "previous_month"
        if observation is None:
            indicator_frequency = session.scalar(
                select(Indicator.frequency).where(
                    Indicator.code == component.code,
                    Indicator.enabled.is_(True),
                )
            )
            if indicator_frequency in {"quarterly", "annual"}:
                observation = _latest_observation(session, component.code, period_end)
                selection = "latest_published_quarter_or_year"
        if observation is None:
            unavailable.append({"indicator_code": component.code, "reason": "No published value"})
            continue
        age = month_age(observation.period_end, period_end)
        if age > component.max_age_months:
            unavailable.append(
                {
                    "indicator_code": component.code,
                    "reason": "Stale value",
                    "source_period_end": str(observation.period_end),
                    "age_months": age,
                    "max_age_months": component.max_age_months,
                }
            )
            continue
        value = D(str(observation.value))
        score = component_score(component.code, value)
        active.append(
            {
                "indicator_code": component.code,
                "indicator_name": component.name,
                "dimension": component.dimension,
                "weight": str(component.weight),
                "normalization_formula": component.formula,
                "value": str(value),
                "component_score": str(score),
                "observation_id": observation.id,
                "source_period_start": str(observation.period_start),
                "source_period_end": str(observation.period_end),
                "age_months": age,
                "unit": observation.unit,
                "raw_artifact_id": observation.raw_artifact_id,
                "source_url": observation.source_url,
                "source_series": observation.source_series,
                "retrieved_at": str(observation.retrieved_at),
                "is_provisional": observation.is_provisional,
                "selection": selection,
            }
        )
    nominal_weight = sum((component.weight for component in COMPONENTS), D("0"))
    used_weight = sum((D(item["weight"]) for item in active), D("0"))
    coverage = used_weight / nominal_weight if nominal_weight else D("0")
    formula = (
        "Nota = Σ(peso_i × clip(normalización_i(valor_i))) / Σ(peso_i de componentes vigentes); "
        "clip(x)=max(0,min(100,x))."
    )
    if not active:
        result = None
        formula_with_values = None
        status = "insufficient_data"
    else:
        weighted_total = sum(
            (D(item["weight"]) * D(item["component_score"]) for item in active), D("0")
        )
        result = (weighted_total / used_weight).quantize(D("0.000001"))
        terms = " + ".join(
            f"{item['weight']}×{item['component_score']} ({item['indicator_code']}="
            f"{item['value']}; {item['source_period_end']})"
            for item in active
        )
        formula_with_values = f"({terms}) / {used_weight} = {result}"
        status = "complete" if coverage == D("1") else "partial"
    inputs = {
        "reference_month": str(period_start)[:7],
        "reference_period_start": str(period_start),
        "reference_period_end": str(period_end),
        "active_components": active,
        "unavailable_components": unavailable,
        "disabled_dimensions": DISABLED_DIMENSIONS,
        "score_interpretation": "0 a 100; condiciones económicas actuales, no bienestar total.",
    }
    signature = hashlib.sha256(
        json.dumps(
            {
                "grade_code": GRADE_CODE,
                "version": METHODOLOGY_VERSION,
                "period_end": str(period_end),
                "inputs": inputs,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    grade = session.scalar(select(CountryGrade).where(CountryGrade.input_signature == signature))
    inserted = False
    if grade is None:
        grade = CountryGrade(
            grade_code=GRADE_CODE,
            geography="ES",
            period_start=period_start,
            period_end=period_end,
            formula=formula,
            formula_with_values=formula_with_values,
            result=result,
            coverage=coverage,
            status=status,
            methodology_version=METHODOLOGY_VERSION,
            inputs_json=inputs,
            calculated_at=datetime.now(UTC),
            input_signature=signature,
        )
        session.add(grade)
        session.flush()
        inserted = True
    return {
        "inserted": inserted,
        "id": grade.id,
        "period_start": str(grade.period_start),
        "period_end": str(grade.period_end),
        "result": str(grade.result) if grade.result is not None else None,
        "coverage": str(grade.coverage),
        "status": grade.status,
        "unavailable_components": unavailable,
    }
