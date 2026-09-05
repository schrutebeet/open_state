from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from civic_metrics.models import Category, Indicator, Source, SourceDataset


class CategoryDefinition(BaseModel):
    code: str
    name: str
    description: str
    display_order: int = 0


class SourceDefinition(BaseModel):
    code: str
    name: str
    base_url: str
    institution_type: str
    is_official: bool = True
    authentication: str = "none"
    credential_names: list[str] = Field(default_factory=list)


class DatasetDefinition(BaseModel):
    code: str
    source: str
    connector: str
    endpoint: str | None = None
    enabled: bool = True
    required: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class ExtractionDefinition(BaseModel):
    kind: Literal[
        "ine_series",
        "bde_series",
        "datacomex_field",
        "html_table_field",
        "html_regex",
        "excel_label",
        "aeat_tax_revenue",
        "social_security_affiliates",
        "social_security_minimum_supplements",
        "derived",
    ]
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    prefer: list[str] = Field(default_factory=list)
    field: str | None = None
    flow_aliases: list[str] = Field(default_factory=list)
    series_code: str | None = None
    sheet_include: list[str] = Field(default_factory=list)
    row_include: list[str] = Field(default_factory=list)
    column_include: list[str] = Field(default_factory=list)
    multiplier: str = "1"
    value_regex: str | None = None


class IndicatorDefinition(BaseModel):
    code: str
    name: str
    description: str
    category: str
    subcategory: str = "general"
    dataset: str | None = None
    unit: str
    frequency: str
    geography: str = "ES"
    direction: str = "contextual"
    enabled: bool = True
    extraction: ExtractionDefinition
    formula: str | None = None
    dependencies: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source_or_formula(self) -> IndicatorDefinition:
        if self.extraction.kind == "derived" and not self.formula:
            raise ValueError(f"Derived indicator {self.code} requires a formula")
        if self.extraction.kind != "derived" and not self.dataset:
            raise ValueError(f"Direct indicator {self.code} requires a dataset")
        return self


class Catalog(BaseModel):
    categories: list[CategoryDefinition]
    sources: list[SourceDefinition]
    datasets: list[DatasetDefinition]
    indicators: list[IndicatorDefinition]

    @property
    def indicator_by_code(self) -> dict[str, IndicatorDefinition]:
        return {item.code: item for item in self.indicators}

    @property
    def dataset_by_code(self) -> dict[str, DatasetDefinition]:
        return {item.code: item for item in self.datasets}


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        content = yaml.safe_load(handle) or {}
    if not isinstance(content, dict):
        raise ValueError(f"Expected a mapping in {path}")
    return content


def load_catalog(config_dir: Path) -> Catalog:
    categories = _load_yaml(config_dir / "categories.yaml").get("categories", [])
    source_config = _load_yaml(config_dir / "sources.yaml")
    indicators = _load_yaml(config_dir / "indicators.yaml").get("indicators", [])
    catalog = Catalog(
        categories=categories,
        sources=source_config.get("sources", []),
        datasets=source_config.get("datasets", []),
        indicators=indicators,
    )
    _validate_references(catalog)
    return catalog


def _validate_references(catalog: Catalog) -> None:
    category_codes = {item.code for item in catalog.categories}
    source_codes = {item.code for item in catalog.sources}
    dataset_codes = {item.code for item in catalog.datasets}
    indicator_codes = {item.code for item in catalog.indicators}

    for dataset in catalog.datasets:
        if dataset.source not in source_codes:
            raise ValueError(f"Dataset {dataset.code} references unknown source {dataset.source}")
    for indicator in catalog.indicators:
        if indicator.category not in category_codes:
            raise ValueError(
                f"Indicator {indicator.code} references unknown category {indicator.category}"
            )
        if indicator.dataset and indicator.dataset not in dataset_codes:
            raise ValueError(
                f"Indicator {indicator.code} references unknown dataset {indicator.dataset}"
            )
        missing = set(indicator.dependencies) - indicator_codes
        if missing:
            raise ValueError(f"Indicator {indicator.code} has unknown dependencies: {missing}")


def sync_catalog(session: Session, catalog: Catalog) -> None:
    categories: dict[str, Category] = {}
    for definition in catalog.categories:
        row = session.scalar(select(Category).where(Category.code == definition.code))
        if row is None:
            row = Category(code=definition.code)
            session.add(row)
        row.name = definition.name
        row.description = definition.description
        row.display_order = definition.display_order
        categories[definition.code] = row
    session.flush()

    sources: dict[str, Source] = {}
    for definition in catalog.sources:
        row = session.scalar(select(Source).where(Source.code == definition.code))
        if row is None:
            row = Source(code=definition.code)
            session.add(row)
        row.name = definition.name
        row.base_url = definition.base_url
        row.institution_type = definition.institution_type
        row.is_official = definition.is_official
        row.authentication = definition.authentication
        row.credential_names = definition.credential_names
        sources[definition.code] = row
    session.flush()

    datasets: dict[str, SourceDataset] = {}
    for definition in catalog.datasets:
        row = session.scalar(select(SourceDataset).where(SourceDataset.code == definition.code))
        if row is None:
            row = SourceDataset(code=definition.code, source_id=sources[definition.source].id)
            session.add(row)
        row.source_id = sources[definition.source].id
        row.connector = definition.connector
        row.endpoint = definition.endpoint
        row.enabled = definition.enabled
        row.config_json = definition.config
        datasets[definition.code] = row
    session.flush()

    for definition in catalog.indicators:
        row = session.scalar(select(Indicator).where(Indicator.code == definition.code))
        dataset_id = datasets[definition.dataset].id if definition.dataset else None
        if row is None:
            row = Indicator(
                code=definition.code,
                category_id=categories[definition.category].id,
                dataset_id=dataset_id,
            )
            session.add(row)
        row.name = definition.name
        row.description = definition.description
        row.subcategory = definition.subcategory
        row.category_id = categories[definition.category].id
        row.dataset_id = dataset_id
        row.unit = definition.unit
        row.frequency = definition.frequency
        row.geography = definition.geography
        row.direction = definition.direction
        row.extraction_json = definition.extraction.model_dump(mode="json")
        row.formula = definition.formula
        row.enabled = definition.enabled
    session.flush()
