"""Read-only comparison of observed period counts in validation databases."""

import argparse
import json
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("databases", nargs="+", type=Path)
    args = parser.parse_args()
    report = {}
    for path in args.databases:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            report[str(path)] = [dict(row) for row in connection.execute('''
                SELECT i.code, i.frequency, COUNT(o.id) AS rows,
                       COUNT(DISTINCT o.period_start || ':' || o.period_end) AS periods,
                       MIN(o.period_start) AS first_period, MAX(o.period_end) AS last_period
                FROM indicators i LEFT JOIN observations o ON o.indicator_id = i.id
                WHERE i.enabled = 1 GROUP BY i.id ORDER BY i.code
            ''')]
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
