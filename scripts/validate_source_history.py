"""Live, read-only source validation; does not modify either application database."""

import argparse
from collections import defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from civic_metrics.catalog import load_catalog
from civic_metrics.connectors import CONNECTORS
from civic_metrics.connectors.base import ConnectorContext
from civic_metrics.http import HttpClient
from civic_metrics.settings import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("datasets", nargs="+")
    parser.add_argument("--lookback", type=int, nargs="+", default=[3, 12, 18])
    args = parser.parse_args()
    catalog = load_catalog(Path(__file__).resolve().parents[1] / "config")
    http = HttpClient(45)
    try:
        for count in args.lookback:
            settings = Settings(lookback_period=count, genai_validation_enabled=False)
            for code in args.datasets:
                dataset = catalog.dataset_by_code[code]
                indicators = [i for i in catalog.indicators if i.dataset == code and i.enabled]
                try:
                    docs = CONNECTORS[dataset.connector]().collect(
                        dataset, ConnectorContext(settings, http, indicators[0].frequency), indicators,
                    )
                    periods = defaultdict(set)
                    for _, rows in docs:
                        for row in rows:
                            periods[row.indicator_code].add(row.period.start)
                    for indicator in indicators:
                        dates = sorted(periods[indicator.code])
                        print(count, indicator.code, len(dates),
                              dates[0] if dates else None, dates[-1] if dates else None,
                              "documents", len(docs), flush=True)
                except Exception as exc:
                    print(count, code, type(exc).__name__, str(exc), flush=True)
    finally:
        http.close()


if __name__ == "__main__":
    main()
