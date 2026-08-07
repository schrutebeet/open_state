# Civic Metrics

**Version 0.1.2**

Local-first Python ingestion backend for a future public dashboard of Spanish economic, fiscal, employment and pension indicators.

The project intentionally contains **no FastAPI application yet**. Its job is to:

1. load a versioned indicator catalog from YAML;
2. download each official dataset only once per run;
3. extract all indicators mapped to that dataset;
4. calculate derived indicators from already stored observations;
5. preserve the original JSON/HTML/XLS/XLSX/CSV artefact;
6. store traceable observations in a relational database.

## Current scope

- 4 categories
- 14 subcategories
- 55 indicators
- 19 source datasets
- Official sources: INE, Banco de España, DataComex, AEAT, IGAE, Seguridad Social and SEPE

See [`docs/indicator-mapping.md`](docs/indicator-mapping.md) for the complete 1:1 mapping.

## Architecture

```text
Official API / publication page
            │
            ▼
      source connector
            │   one fetch per dataset/run
            ▼
 raw artefact archive (hash)
            │
            ▼
 deterministic extraction
            │
            ▼
 candidate validation
            │
            ▼
 SQLAlchemy observation store
            │
            ▼
 deterministic derived KPIs
```

Indicators do not make their own HTTP calls. They are grouped by `dataset` in
`config/indicators.yaml`. For example, a single DataComex response supplies both
`goods_exports` and `goods_imports`; `goods_trade_balance` and
`goods_coverage_ratio` are then calculated from those stored observations.

The HTTP layer also keeps a per-run request cache. This avoids downloading the
same listing page more than once when several files are linked from it.

## Local database and PostgreSQL portability

The default database is:

```text
sqlite+pysqlite:///./data/civic_metrics.db
```

SQLite is **not a PostgreSQL emulator**. It is used as a zero-configuration local
relational database. The schema and queries use portable SQLAlchemy 2 constructs,
so PostgreSQL can be selected by changing one setting:

```bash
export DATABASE_URL='postgresql+psycopg://user:password@host:5432/civic_metrics'
```

Install the PostgreSQL driver with:

```bash
pip install -e '.[postgres]'
```

Before production or collaborative schema development, add Alembic migrations.
`Base.metadata.create_all()` is intentionally sufficient only for this local phase.

## Installation

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e '.[dev]'
```

Run the complete pipeline:

```bash
python main.py
```

JSON summary:

```bash
python main.py --json
```

Run selected datasets only:

```bash
python main.py --dataset ine_gdp_current_prices --dataset bde_public_debt
```

Treat any unavailable source as a failed scheduled run:

```bash
python main.py --strict
```

Inspect stored observations:

```bash
python scripts/inspect_db.py
```

## DataComex credentials

The DataComex API requires a registered username and password. No credential is
stored in YAML, source code, SQLite or a committed text file.

Preferred local setup, using the operating-system credential store:

```bash
python scripts/set_secret.py datacomex_username
python scripts/set_secret.py datacomex_password
```

The connector resolves secrets in this order:

1. explicit runtime setting;
2. process environment variable;
3. operating-system keyring.

Environment-variable alternative:

```bash
export DATACOMEX_USERNAME='...'
export DATACOMEX_PASSWORD='...'
```

On a server, inject these values through the hosting provider's secrets manager,
systemd credentials, Docker secrets or another external secret store. Do not copy
real values into `.env.example` or any committed file.

If credentials are absent, the DataComex dataset fails independently while the
other sources continue.

## GenAI dataset validation

An optional semantic validation call can compare each fetched payload with the
normalized observations passed to the database. JSON, HTML and text payloads are
rendered directly; Excel workbooks are rendered as tab-separated sheet data.

Set the API key outside the repository and enable validation:

```bash
export OPENAI_API_KEY='...'
export GENAI_VALIDATION_ENABLED=true
```

The default model is `gpt-5.6-luna`. Validation is advisory: its structured report
appears under each dataset's `genai_validation` field in `--json` output, while API
errors do not stop ingestion. To mark an otherwise successful dataset as `partial`
when the model finds a mismatch, also set:

```bash
export GENAI_VALIDATION_STRICT=true
```

Payload text sent to the API is capped at 60,000 characters by default. Override
this with `GENAI_VALIDATION_MAX_PAYLOAD_CHARS`. Because this sends
source data to OpenAI and model judgments are probabilistic, keep deterministic
schema and range checks as the authoritative validation layer.

## Configuration

### `config/categories.yaml`

Display categories and their order.

### `config/sources.yaml`

Official institutions, authentication metadata, source datasets, endpoints and
connector settings.

### `config/indicators.yaml`

The stable indicator contract. Each direct indicator defines:

- code, name and description;
- category and subcategory;
- unit, geography and frequency;
- source dataset;
- exact extraction selector.

Derived indicators declare dependencies and a deterministic formula. Supported
formula operations are intentionally limited to arithmetic plus:

```text
rolling_sum(indicator, periods)
pct_change(indicator, periods)
```

No Python code is evaluated from YAML.

## Database lineage

Every observation stores:

- indicator;
- source and dataset codes;
- period and frequency;
- value and unit;
- source series/cell;
- original source URL;
- retrieval time;
- original raw artefact;
- extraction metadata.

A revised official value is inserted as another observation rather than replacing
history. Derived observations link to the exact source observation IDs used in the
calculation through `observation_dependencies`.

## Raw artefacts

Downloads are archived under:

```text
artifacts/<source>/<dataset>/<date>/
```

The filename includes a SHA-256 prefix. This allows extraction bugs to be replayed
without repeatedly contacting the source and provides evidence for every stored
number.

## Running twice per day

The process is idempotent at observation level and uses `data/pipeline.lock` to
prevent overlapping runs.

Example cron entry at 07:15 and 19:15:

```cron
15 7,19 * * * cd /absolute/path/civic_metrics && .venv/bin/python main.py --strict >> logs/pipeline.log 2>&1
```

Most official indicators update monthly or quarterly, so twice-daily execution is
useful for publication discovery but does not imply that the underlying value will
change twice per day.

## Failure behaviour

Each source is isolated. A changed workbook layout or unavailable website produces
a dataset-level error in the run summary without discarding successful sources.
Raw files are stored before database insertion, and candidates are validated before
being saved.

HTML/XLS selectors in this initial version are deliberately isolated in YAML and
connector classes because government publication layouts can change. A first live
run should be reviewed against the archived artefact before relying on an adapter
for unattended publication.

## Tests

```bash
pytest -q
```

The current tests cover catalog integrity, SQLite schema creation, multi-indicator
INE extraction, CSV/XLS-style label extraction and deterministic derived indicators
with dependency lineage.

## Next backend milestones

1. source-specific validation rules and anomaly thresholds;
2. publication/review states instead of immediate visibility;
3. Alembic migrations;
4. structured run/error tables per dataset;
5. integration tests with periodically refreshed official fixtures;
6. only then, a read-only FastAPI layer.

## Live-source compatibility fixes in 0.1.1

This revision incorporates the first live execution against the official sites:

- INE selectors now match the actual API series names and prefer seasonally adjusted GDP series;
- the EPA HTML parser skips title/header rows and selects the newest parseable quarter;
- quarterly labels such as `2T 2026`, `T2 2026` and `2026T2` are supported;
- multi-row and merged Excel headers are reconstructed before column matching;
- Excel download discovery also searches the surrounding table row, which is required by
  Seguridad Social and SEPE pages whose links are labelled only `(XLS, ... KB)`;
- the exact Seguridad Social pensioner publication endpoint is configured;
- missing optional DataComex credentials produce `SKIPPED`, not a misleading source failure;
- a dataset that downloads correctly but matches no observations produces `EMPTY`, not `SUCCESS`.

For a clean verification after upgrading from 0.1.0, remove the local test database first so
that observations produced by old selectors do not remain in the test history:

```bash
rm -f data/civic_metrics.db
python main.py --json
```

The raw downloads remain under `artifacts/`. To inspect an XLS/XLSX/CSV whose layout still
needs a source-specific selector:

```bash
python scripts/inspect_workbook.py artifacts/path/to/file.xlsx --rows 60
python scripts/inspect_workbook.py artifacts/path/to/file.xlsx --contains total
```

## Source-specific workbook support

Version 0.1.3 includes dedicated parsers for the official Social Security pension workbooks (`SYYYYMM.xlsx`, `ICONCEPTOSYYYYMM.xlsx`, and `PTASYYYYMM.xlsx`) and the SEPE `evolparo.xls` workbook. See `docs/workbook-parsers.md`.

### SEPE prerequisite

The official `evolparo.xls` file contains malformed legacy Excel metadata. Civic Metrics converts it locally with LibreOffice before reading it.

Windows: install LibreOffice and ensure its program directory is on `PATH` (commonly `C:\Program Files\LibreOffice\program`).

Linux:

```bash
sudo apt-get install libreoffice-calc
```

macOS:

```bash
brew install --cask libreoffice
```

## v0.1.4 live-format fixes

Version 0.1.4 adds dedicated IGAE parsers, corrects the unit conversion in State budget
execution, fixes the statistical period selected from Social Security articles, and reports
partially extracted datasets explicitly. See `docs/live-run-validation-v014.md`.
