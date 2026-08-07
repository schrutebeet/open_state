# Architecture decisions

## Monolithic package, separate responsibilities

This phase uses one Python package, but connectors, parsers, processors, persistence
and orchestration are independent modules. A future worker process or API can import
them without moving business logic.

## Dataset-first ingestion

HTTP calls belong to datasets, not indicators. This prevents repeated calls and
makes source artefacts first-class objects. Several indicators can consume one
payload, and derived indicators consume the database rather than the network.

## Immutable observations

Revisions create new observations. The latest value can later be selected by API
query, while historical revisions remain auditable.

## Portable SQLAlchemy schema

The local database is SQLite. PostgreSQL is the intended server database. No
SQLite-only query feature is used in domain persistence code.

## Deterministic transformations

The LLM layer is intentionally absent. Every current number comes from a source
selector or a public formula whose inputs are recorded.
