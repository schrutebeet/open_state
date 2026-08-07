from __future__ import annotations

import argparse
from pathlib import Path

from civic_metrics.parsers.excel import WorkbookMatrix


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print the non-empty structure of an archived XLS/XLSX/CSV workbook."
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("--rows", type=int, default=40, help="Maximum non-empty rows per sheet")
    parser.add_argument(
        "--contains",
        action="append",
        default=[],
        help="Only print rows containing this text; may be repeated",
    )
    args = parser.parse_args()

    path = args.path.expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"File does not exist: {path}")

    suffix = path.suffix.lower()
    content_type = {
        ".csv": "text/csv",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(suffix, "application/octet-stream")
    workbook = WorkbookMatrix.from_bytes(path.read_bytes(), content_type, path.as_uri())
    needles = [item.casefold() for item in args.contains]

    for sheet_name, rows in workbook.sheets.items():
        print(f"\n=== {sheet_name} ===")
        printed = 0
        for index, row in enumerate(rows, start=1):
            values = [str(value).strip() for value in row if value is not None and str(value).strip()]
            if not values:
                continue
            rendered = " | ".join(values)
            if needles and not all(needle in rendered.casefold() for needle in needles):
                continue
            print(f"{index:>5}: {rendered[:500]}")
            printed += 1
            if printed >= args.rows:
                break


if __name__ == "__main__":
    main()
