"""Generate country grades in history.db or snapshot.db."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from civic_metrics.country_grade_runner import main  # noqa: E402

if __name__ == "__main__":
    main()
