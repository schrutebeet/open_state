from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from civic_metrics.catalog import IndicatorDefinition
from civic_metrics.domain import ObservationCandidate


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


class CandidateValidationError(ValueError):
    def __init__(self, indicator_code: str, issues: list[ValidationIssue]) -> None:
        self.indicator_code = indicator_code
        self.issues = issues
        joined = "; ".join(f"{item.code}: {item.message}" for item in issues)
        super().__init__(f"Invalid candidate for {indicator_code}: {joined}")


def validate_candidate(
    candidate: ObservationCandidate,
    definition: IndicatorDefinition,
    *,
    today: date | None = None,
) -> None:
    """Validate source identity and schema before an observation reaches the DB.

    These checks are intentionally deterministic. Source-specific statistical
    validation can be added later without changing connector contracts.
    """
    issues: list[ValidationIssue] = []
    today = today or date.today()
    if candidate.indicator_code != definition.code:
        issues.append(ValidationIssue("indicator_mismatch", "indicator code differs from catalog"))
    if candidate.unit != definition.unit:
        issues.append(
            ValidationIssue(
                "unit_mismatch",
                f"expected {definition.unit}, received {candidate.unit}",
            )
        )
    if candidate.period.frequency != definition.frequency:
        issues.append(
            ValidationIssue(
                "frequency_mismatch",
                f"expected {definition.frequency}, received {candidate.period.frequency}",
            )
        )
    if candidate.period.start > candidate.period.end:
        issues.append(ValidationIssue("invalid_period", "period start is after period end"))
    if candidate.period.start.year < 1900:
        issues.append(ValidationIssue("invalid_period", "period predates supported history"))
    if candidate.period.start.year > today.year + 1:
        issues.append(ValidationIssue("future_period", "period is implausibly far in the future"))
    if not isinstance(candidate.value, Decimal) or not candidate.value.is_finite():
        issues.append(ValidationIssue("invalid_value", "value must be a finite Decimal"))
    if not candidate.source_code or not candidate.dataset_code:
        issues.append(ValidationIssue("missing_lineage", "source and dataset are required"))
    if definition.extraction.kind != "derived" and not candidate.source_url:
        issues.append(ValidationIssue("missing_source_url", "direct observations require a source URL"))
    if issues:
        raise CandidateValidationError(candidate.indicator_code, issues)
