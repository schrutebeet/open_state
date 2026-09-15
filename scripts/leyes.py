"""Query the structured official lifecycle of Spanish state ordinary laws."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from civic_metrics.http import HttpClient  # noqa: E402
from civic_metrics.legislation import Ley, parse_date  # noqa: E402
from civic_metrics.legislation_store import LawHistoryStore  # noqa: E402


def _display_count(value: int | None) -> str:
    return "no disponible" if value is None else str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        dest="target_date",
        default=date.today().isoformat(),
        help="Fecha de consulta en formato YYYY-MM-DD (por defecto: hoy)",
    )
    parser.add_argument(
        "--legislature",
        default="15",
        help="Legislatura del Senado a consultar (por defecto: 15)",
    )
    parser.add_argument(
        "--include-government",
        action="store_true",
        help="Añadir aprobaciones de proyectos detectadas en RSS/HTML de La Moncloa",
    )
    parser.add_argument("--json", action="store_true", help="Emitir el informe como JSON")
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "data" / "history.db",
        help="Ruta de history.db (por defecto: data/history.db)",
    )
    parser.add_argument(
        "--history-only",
        action="store_true",
        help="No consulta Internet: muestra solo los eventos ya guardados en history.db",
    )
    args = parser.parse_args()

    target_date = parse_date(args.target_date)
    if args.history_only:
        with LawHistoryStore(args.database) as store:
            report = store.report_for_day(target_date)
        if not report.source_status:
            print(
                f"No hay una consulta guardada para {target_date.isoformat()} en {args.database}.",
                file=sys.stderr,
            )
            return 2
    else:
        client = HttpClient(45)
        try:
            report = Ley.resumen_dia(
                target_date,
                client=client,
                legislature=args.legislature,
                include_government=args.include_government,
            )
        finally:
            client.close()
        with LawHistoryStore(args.database) as store:
            store.record_report(report)

    counts = report.verified_counts()
    candidates = report.candidate_counts()
    if args.json:
        payload = report.to_dict()
        payload["database"] = str(args.database)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"Leyes ordinarias - {report.target_date.isoformat()}")
    print(f"  Iniciadas (confirmadas): {_display_count(counts['iniciadas'])}")
    print(
        "  Aprobadas definitivamente (confirmadas): "
        f"{_display_count(counts['aprobadas_definitivamente'])}"
    )
    print(
        "  Sancionadas/promulgadas (confirmadas): "
        f"{_display_count(counts['sancionadas_promulgadas'])}"
    )
    print(f"  Publicadas en BOE (confirmadas): {_display_count(counts['publicadas_boe'])}")
    print(
        "  Entrada en vigor (confirmadas): "
        f"{_display_count(counts['entradas_en_vigor'])}"
    )
    if args.include_government:
        print(
            "  Aprobadas por el Gobierno (confirmadas): "
            f"{_display_count(counts['aprobadas_por_gobierno'])}"
        )
    candidate_labels = {
        "aprobadas_definitivamente": "Aprobaciones definitivas candidatas",
        "sancionadas_promulgadas": "Sanciones/promulgaciones candidatas",
        "aprobadas_por_gobierno": "Aprobaciones del Gobierno candidatas",
    }
    candidate_lines = [
        f"  {label}: {candidates[metric]}"
        for metric, label in candidate_labels.items()
        if candidates[metric]
    ]
    if candidate_lines:
        print("Candidatas (requieren verificación adicional):")
        for line in candidate_lines:
            print(line)
    if report.warnings:
        print("Avisos:")
        for warning in report.warnings:
            print(f"  - {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
