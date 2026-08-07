from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from civic_metrics.db import create_database_engine, make_session_factory  # noqa: E402
from civic_metrics.models import Indicator, Observation  # noqa: E402
from civic_metrics.settings import Settings  # noqa: E402


def main() -> None:
    settings = Settings(project_root=ROOT, _env_file=ROOT / ".env")
    factory = make_session_factory(create_database_engine(settings.resolved_database_url()))
    with factory() as session:
        rows = session.execute(
            select(Indicator.code, Observation.period_label, Observation.value, Observation.unit)
            .join(Observation)
            .order_by(Indicator.code, Observation.period_end.desc())
        ).all()
    for code, period, value, unit in rows:
        print(f"{code:42} {period:12} {value} {unit}")


if __name__ == "__main__":
    main()
