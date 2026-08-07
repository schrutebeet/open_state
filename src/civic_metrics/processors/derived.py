from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DivisionByZero
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from civic_metrics.catalog import IndicatorDefinition
from civic_metrics.domain import ObservationCandidate, Period
from civic_metrics.models import Indicator, Observation
from civic_metrics.repository import add_dependencies, decimal_value, save_observation


class FormulaError(ValueError):
    """Raised when a derived-indicator formula is unsafe or cannot be evaluated."""


@dataclass(frozen=True)
class EvaluationResult:
    value: Decimal
    dependencies: tuple[Observation, ...]
    period: Period


class DerivedIndicatorEngine:
    """Evaluate a deliberately small, deterministic formula language.

    Supported syntax:
      - indicator names: ``goods_exports``
      - +, -, *, / and parentheses
      - ``rolling_sum(indicator, periods)``
      - ``pct_change(indicator, periods)``

    The evaluator never executes Python code and records every source observation
    used to produce the result.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def materialise(self, definition: IndicatorDefinition) -> Observation | None:
        if not definition.formula:
            raise FormulaError(f"Indicator {definition.code} has no formula")
        result = self.evaluate(definition)
        if result is None:
            return None

        dependency_signature = hashlib.sha256(
            ",".join(str(item.id) for item in result.dependencies).encode("ascii")
        ).hexdigest()[:16]
        candidate = ObservationCandidate(
            indicator_code=definition.code,
            source_code="derived",
            dataset_code="derived",
            period=result.period,
            value=result.value,
            unit=definition.unit,
            source_series=f"{definition.formula}#{dependency_signature}",
            metadata={
                "formula": definition.formula,
                "dependency_indicator_codes": definition.dependencies,
                "dependency_observation_ids": [item.id for item in result.dependencies],
            },
            dependencies=tuple(definition.dependencies),
        )
        observation = save_observation(self.session, candidate, artifact=None)
        add_dependencies(self.session, observation, list(result.dependencies))
        self.session.flush()
        return observation

    def evaluate(self, definition: IndicatorDefinition) -> EvaluationResult | None:
        target = self._target_observation(definition.dependencies)
        if target is None:
            return None
        target_period = Period(
            start=target.period_start,
            end=target.period_end,
            label=target.period_label,
            frequency=definition.frequency,
        )
        tree = ast.parse(definition.formula or "", mode="eval")
        referenced_names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
            and node.id not in {"rolling_sum", "pct_change"}
        }
        undeclared = referenced_names - set(definition.dependencies)
        if undeclared:
            raise FormulaError(
                f"Formula references undeclared dependencies: {sorted(undeclared)}"
            )
        value, dependencies = self._evaluate_node(tree.body, target_period)
        unique = {item.id: item for item in dependencies}
        return EvaluationResult(value=value, dependencies=tuple(unique.values()), period=target_period)

    def _target_observation(self, dependency_codes: list[str]) -> Observation | None:
        if not dependency_codes:
            raise FormulaError("A derived indicator requires at least one dependency")
        latest_by_dependency: list[Observation] = []
        for code in dependency_codes:
            row = self._latest(code)
            if row is None:
                return None
            latest_by_dependency.append(row)
        # Use the oldest common frontier, preventing a monthly/quarterly mismatch
        # from silently using a future observation from one dependency.
        target_end = min(item.period_end for item in latest_by_dependency)
        return max(
            (item for item in latest_by_dependency if item.period_end <= target_end),
            key=lambda item: item.period_end,
        )

    def _evaluate_node(
        self,
        node: ast.AST,
        target_period: Period,
    ) -> tuple[Decimal, list[Observation]]:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str)):
            return Decimal(str(node.value)), []
        if isinstance(node, ast.Name):
            observation = self._observation_at_or_before(node.id, target_period.end)
            if observation is None:
                raise FormulaError(
                    f"No observation for {node.id} at or before {target_period.end.isoformat()}"
                )
            return decimal_value(observation), [observation]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value, dependencies = self._evaluate_node(node.operand, target_period)
            return ((value if isinstance(node.op, ast.UAdd) else -value), dependencies)
        if isinstance(node, ast.BinOp) and isinstance(
            node.op,
            (ast.Add, ast.Sub, ast.Mult, ast.Div),
        ):
            left, left_dependencies = self._evaluate_node(node.left, target_period)
            right, right_dependencies = self._evaluate_node(node.right, target_period)
            operations: dict[type[ast.operator], Callable[[Decimal, Decimal], Decimal]] = {
                ast.Add: lambda a, b: a + b,
                ast.Sub: lambda a, b: a - b,
                ast.Mult: lambda a, b: a * b,
                ast.Div: lambda a, b: a / b,
            }
            try:
                value = operations[type(node.op)](left, right)
            except (DivisionByZero, ZeroDivisionError) as exc:
                raise FormulaError("Formula attempted division by zero") from exc
            return value, left_dependencies + right_dependencies
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise FormulaError("Only named formula functions are allowed")
            if len(node.args) != 2 or not isinstance(node.args[0], ast.Name):
                raise FormulaError(
                    f"{node.func.id} requires an indicator name and a number of periods"
                )
            periods_value, _ = self._evaluate_node(node.args[1], target_period)
            periods = int(periods_value)
            if periods <= 0:
                raise FormulaError("Number of periods must be positive")
            indicator_code = node.args[0].id
            if node.func.id == "rolling_sum":
                observations = self._history(indicator_code, target_period.end, periods)
                if len(observations) < periods:
                    raise FormulaError(
                        f"rolling_sum({indicator_code}, {periods}) needs {periods} observations"
                    )
                return sum((decimal_value(item) for item in observations), Decimal("0")), observations
            if node.func.id == "pct_change":
                observations = self._history(indicator_code, target_period.end, periods + 1)
                if len(observations) < periods + 1:
                    raise FormulaError(
                        f"pct_change({indicator_code}, {periods}) needs {periods + 1} observations"
                    )
                current = decimal_value(observations[0])
                previous = decimal_value(observations[periods])
                if previous == 0:
                    raise FormulaError("pct_change base observation is zero")
                return (current / previous - Decimal("1")) * Decimal("100"), [
                    observations[0],
                    observations[periods],
                ]
            raise FormulaError(f"Unsupported formula function: {node.func.id}")
        raise FormulaError(f"Unsupported formula syntax: {ast.dump(node)}")

    def _latest(self, indicator_code: str) -> Observation | None:
        return self.session.scalar(
            select(Observation)
            .join(Indicator)
            .where(Indicator.code == indicator_code)
            .order_by(Observation.period_end.desc(), Observation.retrieved_at.desc())
            .limit(1)
        )

    def _observation_at_or_before(
        self,
        indicator_code: str,
        period_end: date,
    ) -> Observation | None:
        return self.session.scalar(
            select(Observation)
            .join(Indicator)
            .where(
                Indicator.code == indicator_code,
                Observation.period_end <= period_end,
            )
            .order_by(Observation.period_end.desc(), Observation.retrieved_at.desc())
            .limit(1)
        )

    def _history(
        self,
        indicator_code: str,
        period_end: date,
        count: int,
    ) -> list[Observation]:
        rows = list(
            self.session.scalars(
                select(Observation)
                .join(Indicator)
                .where(
                    Indicator.code == indicator_code,
                    Observation.period_end <= period_end,
                )
                .order_by(Observation.period_end.desc(), Observation.retrieved_at.desc())
            )
        )
        # If a source republishes or revises a period, use the most recently
        # retrieved observation for that period and count periods only once.
        distinct: list[Observation] = []
        seen: set[tuple[date, date]] = set()
        for row in rows:
            key = (row.period_start, row.period_end)
            if key in seen:
                continue
            seen.add(key)
            distinct.append(row)
            if len(distinct) == count:
                break
        return distinct
