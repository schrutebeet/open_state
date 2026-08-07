from io import BytesIO

import openpyxl

from civic_metrics.parsers.excel import WorkbookMatrix


def test_multirow_excel_headers_are_combined() -> None:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "PRESUPUESTO DE INGRESOS"
    sheet.append(["Concepto", "2026", None, None])
    sheet.append([None, "Previsiones presupuestarias", "Derechos reconocidos netos", "Recaudación neta"])
    sheet.append(["TOTAL", 1000, 500, 450])
    buffer = BytesIO()
    book.save(buffer)

    workbook = WorkbookMatrix.from_bytes(
        buffer.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "https://example.test/extracto.xlsx",
    )
    match = workbook.find_value(
        row_include=["Total"],
        column_include=["derechos reconocidos netos"],
    )
    assert str(match.value) == "500"
    assert "Derechos reconocidos netos" in (match.column_label or "")
