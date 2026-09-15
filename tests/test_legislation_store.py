from datetime import date

from civic_metrics.legislation import (
    EVENT_PUBLICATION,
    EVENT_SANCTION_PROMULGATION,
    DailyLawReport,
    LegislativeEvent,
    Ley,
)
from civic_metrics.legislation_store import LawHistoryStore


def test_history_store_joins_a_law_by_its_statutory_number_and_keeps_events(tmp_path) -> None:
    day = date(2025, 7, 25)
    eli_law = Ley(
        "Ley 4/2025, de 24 de julio, de prueba.",
        metadata={"url_eli": "https://www.boe.es/eli/es/l/2025/07/24/4"},
    )
    eli_law.add_event(
        LegislativeEvent(
            EVENT_SANCTION_PROMULGATION,
            date(2025, 7, 24),
            "boe_eli_daily",
            "https://www.boe.es/eli/es/l/2025/07/24/4",
        )
    )
    boe_law = Ley("Ley 4/2025, de 24 de julio, de prueba.", boe_id="BOE-A-2025-15423")
    boe_law.add_event(
        LegislativeEvent(
            EVENT_PUBLICATION,
            day,
            "boe_document_xml",
            "https://www.boe.es/diario_boe/xml.php?id=BOE-A-2025-15423",
        )
    )
    report = DailyLawReport(
        day,
        [boe_law],
        source_status={"boe_sumario": "ok", "boe_eli": "ok"},
        discovered_laws=[eli_law, boe_law],
    )

    with LawHistoryStore(tmp_path / "history.db") as store:
        store.record_report(report)
        store.record_report(report)
        saved = store.report_for_day(day)
        law_count = store.db.execute("SELECT COUNT(*) FROM legislation_laws").fetchone()[0]
        event_count = store.db.execute("SELECT COUNT(*) FROM legislation_events").fetchone()[0]

    assert law_count == 1
    assert event_count == 2
    assert len(saved.published) == 1
    assert saved.published[0].boe_id == "BOE-A-2025-15423"
