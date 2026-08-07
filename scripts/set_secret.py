from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from civic_metrics.security import set_secret  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Store a Civic Metrics secret in the OS keyring.")
    parser.add_argument(
        "name",
        choices=["datacomex_username", "datacomex_password"],
    )
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    value = args.value or getpass.getpass(f"Value for {args.name}: ")
    set_secret(args.name, value)
    print(f"Stored {args.name} in the operating-system keyring.")


if __name__ == "__main__":
    main()
