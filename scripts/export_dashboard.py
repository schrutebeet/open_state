#!/usr/bin/env python3
"""Export a small, public, frontend-safe snapshot from civic_metrics.db."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SPAIN_TIMEZONE = ZoneInfo("Europe/Madrid")
REQUIRED_CODES = {
    "gdp_real_yoy",
    "cpi_yoy",
    "public_debt_gdp",
    "general_government_balance_gdp",
    "unemployment_rate",
    "registered_unemployment",
    "pension_monthly_payroll",
    "affiliates_per_pensioner",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=os.environ.get("DB_PATH", "data/civic_metrics.db"),
        help="Path to the SQLite database.",
    )
    parser.add_argument(
        "--output",
        default="public/dashboard.json",
        help="Destination JSON file.",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=24,
        help="Maximum observations exported per indicator.",
    )
    return parser.parse_args()


def _observations(
    connection: sqlite3.Connection,
    indicator_id: int,
    limit: int,
) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT
            id,
            period_start,
            period_end,
            period_label,
            value,
            status,
            is_provisional,
            source_code,
            dataset_code,
            source_url,
            published_at,
            retrieved_at
        FROM observations
        WHERE indicator_id = ? AND status = 'published'
        ORDER BY period_end DESC, retrieved_at DESC, id DESC
        """,
        (indicator_id,),
    ).fetchall()

    # A revised value may coexist with an older value for the same period.
    # Keep the most recently retrieved observation for each period.
    selected: list[sqlite3.Row] = []
    seen_periods: set[tuple[str, str]] = set()
    for row in rows:
        period = (row["period_start"], row["period_end"])
        if period in seen_periods:
            continue
        seen_periods.add(period)
        selected.append(row)
        if len(selected) == limit:
            break

    return [
        {
            "periodStart": row["period_start"],
            "periodEnd": row["period_end"],
            "periodLabel": row["period_label"],
            "value": float(row["value"]),
            "isProvisional": bool(row["is_provisional"]),
            "sourceCode": row["source_code"],
            "datasetCode": row["dataset_code"],
            "sourceUrl": row["source_url"],
            "publishedAt": row["published_at"],
            "retrievedAt": row["retrieved_at"],
        }
        for row in reversed(selected)
    ]


def export_dashboard(database: Path, output: Path, history_limit: int) -> None:
    if not database.is_file():
        raise SystemExit(f"Database not found: {database}")
    if history_limit < 1:
        raise SystemExit("--history-limit must be at least 1")

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        indicator_rows = connection.execute(
            """
            SELECT
                i.id,
                i.code,
                i.name,
                i.description,
                i.subcategory,
                i.unit,
                i.frequency,
                i.geography,
                i.direction,
                c.code AS category_code,
                c.name AS category_name,
                sd.code AS configured_dataset_code,
                s.code AS configured_source_code,
                s.name AS configured_source_name,
                s.base_url AS configured_source_url
            FROM indicators AS i
            JOIN categories AS c ON c.id = i.category_id
            LEFT JOIN source_datasets AS sd ON sd.id = i.dataset_id
            LEFT JOIN sources AS s ON s.id = sd.source_id
            WHERE i.enabled = 1
            ORDER BY c.display_order, i.id
            """
        ).fetchall()

        indicators: dict[str, dict[str, object]] = {}
        for row in indicator_rows:
            history = _observations(connection, row["id"], history_limit)
            if not history:
                continue
            latest = history[-1]
            indicators[row["code"]] = {
                "code": row["code"],
                "name": row["name"],
                "description": row["description"],
                "category": row["category_code"],
                "categoryName": row["category_name"],
                "subcategory": row["subcategory"],
                "unit": row["unit"],
                "frequency": row["frequency"],
                "geography": row["geography"],
                "direction": row["direction"],
                "source": {
                    "code": row["configured_source_code"] or latest["sourceCode"],
                    "name": row["configured_source_name"],
                    "datasetCode": row["configured_dataset_code"] or latest["datasetCode"],
                    "url": latest["sourceUrl"] or row["configured_source_url"],
                },
                "observations": history,
            }

    missing = sorted(REQUIRED_CODES - indicators.keys())
    if missing:
        raise SystemExit(
            "Refusing to publish: required indicators have no published observations: "
            + ", ".join(missing)
        )

    generated_at = datetime.now(SPAIN_TIMEZONE).isoformat(timespec="seconds")
    payload = {
        "schemaVersion": "1.0",
        "generatedAt": generated_at,
        "timezone": "Europe/Madrid",
        "indicatorCount": len(indicators),
        "indicators": indicators,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    print(f"Exported {len(indicators)} indicators to {output} at {generated_at}")


def main() -> None:
    args = parse_args()
    export_dashboard(
        Path(args.database),
        Path(args.output),
        args.history_limit,
    )


if __name__ == "__main__":
    main()
