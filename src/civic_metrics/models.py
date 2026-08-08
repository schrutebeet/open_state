from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    indicators: Mapped[list[Indicator]] = relationship(back_populates="category")


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(240))
    base_url: Mapped[str] = mapped_column(Text)
    institution_type: Mapped[str] = mapped_column(String(80))
    is_official: Mapped[bool] = mapped_column(Boolean, default=True)
    authentication: Mapped[str] = mapped_column(String(80), default="none")
    credential_names: Mapped[list[str]] = mapped_column(JSON, default=list)

    datasets: Mapped[list[SourceDataset]] = relationship(back_populates="source")


class SourceDataset(Base):
    __tablename__ = "source_datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    connector: Mapped[str] = mapped_column(String(80))
    endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    source: Mapped[Source] = relationship(back_populates="datasets")
    indicators: Mapped[list[Indicator]] = relationship(back_populates="dataset")


class Indicator(Base):
    __tablename__ = "indicators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    subcategory: Mapped[str] = mapped_column(String(120), default="general", index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_datasets.id"), nullable=True, index=True
    )
    unit: Mapped[str] = mapped_column(String(80))
    frequency: Mapped[str] = mapped_column(String(40))
    geography: Mapped[str] = mapped_column(String(20), default="ES")
    direction: Mapped[str] = mapped_column(String(40), default="contextual")
    extraction_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    formula: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    category: Mapped[Category] = relationship(back_populates="indicators")
    dataset: Mapped[SourceDataset | None] = relationship(back_populates="indicators")
    observations: Mapped[list[Observation]] = relationship(back_populates="indicator")


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="running")
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    artifacts: Mapped[list[RawArtifact]] = relationship(back_populates="run")
    genai_validation_logs: Mapped[list[GenAIValidationLog]] = relationship(back_populates="run")


class RawArtifact(Base):
    __tablename__ = "raw_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("ingestion_runs.id"), index=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("source_datasets.id"), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(160))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    local_path: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    run: Mapped[IngestionRun] = relationship(back_populates="artifacts")


class GenAIValidationLog(Base):
    """Audit record for one GenAI validation attempt."""

    __tablename__ = "genai_validation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("ingestion_runs.id"), index=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("source_datasets.id"), index=True)
    raw_artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_artifacts.id"), nullable=True, index=True
    )
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 6), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    request_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    run: Mapped[IngestionRun] = relationship(back_populates="genai_validation_logs")


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint(
            "indicator_id",
            "period_start",
            "period_end",
            "geography",
            "source_code",
            "dataset_code",
            "source_series",
            "value",
            name="uq_observation_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    indicator_id: Mapped[int] = mapped_column(ForeignKey("indicators.id"), index=True)
    raw_artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_artifacts.id"), nullable=True, index=True
    )
    source_code: Mapped[str] = mapped_column(String(80), index=True)
    dataset_code: Mapped[str] = mapped_column(String(120), index=True)
    period_start: Mapped[date] = mapped_column(Date, index=True)
    period_end: Mapped[date] = mapped_column(Date, index=True)
    period_label: Mapped[str] = mapped_column(String(80))
    frequency: Mapped[str] = mapped_column(String(40))
    geography: Mapped[str] = mapped_column(String(20), default="ES")
    value: Mapped[Decimal] = mapped_column(Numeric(30, 10))
    unit: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="published")
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=False)
    source_series: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    indicator: Mapped[Indicator] = relationship(back_populates="observations")
    dependencies: Mapped[list[ObservationDependency]] = relationship(
        foreign_keys="ObservationDependency.observation_id",
        cascade="all, delete-orphan",
    )


class ObservationDependency(Base):
    __tablename__ = "observation_dependencies"
    __table_args__ = (UniqueConstraint("observation_id", "depends_on_observation_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("observations.id"), index=True)
    depends_on_observation_id: Mapped[int] = mapped_column(
        ForeignKey("observations.id"), index=True
    )
