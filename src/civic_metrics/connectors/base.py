from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from civic_metrics.catalog import DatasetDefinition, IndicatorDefinition
from civic_metrics.domain import DatasetPayload, ObservationCandidate
from civic_metrics.http import HttpClient
from civic_metrics.settings import Settings


@dataclass(frozen=True)
class ConnectorContext:
    settings: Settings
    http: HttpClient
    frequency: str | None = None


class Connector(ABC):
    connector_name: str

    def collect(self, dataset, context, indicators):
        """Keep each downloaded document paired with the observations it supports."""
        payload = self.fetch(dataset, context)
        return [(payload, self.extract(dataset, payload, indicators))]

    @abstractmethod
    def fetch(self, dataset: DatasetDefinition, context: ConnectorContext) -> DatasetPayload:
        raise NotImplementedError

    @abstractmethod
    def extract(
        self,
        dataset: DatasetDefinition,
        payload: DatasetPayload,
        indicators: list[IndicatorDefinition],
    ) -> list[ObservationCandidate]:
        raise NotImplementedError


def lookback_periods(context: ConnectorContext, frequency: str) -> int:
    """Return the number of observations requested for one indicator."""
    if context.frequency is not None and context.frequency != frequency:
        raise ValueError(
            f"Dataset frequency {context.frequency!r} does not match connector frequency {frequency!r}"
        )
    return context.settings.lookback_period
