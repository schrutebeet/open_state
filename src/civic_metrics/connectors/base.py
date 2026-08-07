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


class Connector(ABC):
    connector_name: str

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
