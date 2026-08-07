from civic_metrics.parsers.excel import WorkbookMatrix


def test_csv_parser_and_label_extraction() -> None:
    body = "Concepto;enero 2026;febrero 2026\nTotal ingresos;100;120\n".encode()
    workbook = WorkbookMatrix.from_bytes(body, "text/csv", "https://example.test/data.csv")
    match = workbook.find_value(row_include=["Total ingresos"])
    assert str(match.value) == "120"
    assert match.column_label == "febrero 2026"
