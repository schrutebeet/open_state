# Live run fixes — 0.1.1

The first live run identified several classes of integration issue. This revision addresses them without changing the database contract.

## INE

- Match the exact current-price and chain-linked-volume series names returned by `DATOS_TABLA`.
- Prefer seasonally and calendar-adjusted GDP series when more than one series matches.
- Match core inflation through `General sin alimentos no elaborados ni productos energéticos`.
- Use the published 20–24 age band instead of claiming that table 65219 contains one 16–24 aggregate.
- Match labour-cost series by independent terms instead of assuming their order in the generated series name.

## EPA HTML table

- Ignore headings such as `Trimestre`.
- Search all rows for parseable periods and choose the newest quarter.
- Find the actual field-header row immediately above the selected data row.

## XLS/XLSX discovery and parsing

- Use the surrounding `<tr>` or `<li>` text when an official download link itself only says `(XLS, ... KB)`.
- Reconstruct multi-row Excel column context, including horizontally merged headings.
- Infer publication periods from the listing text, column heading, worksheet name or workbook content, in that order.
- Add diagnostics showing nearby workbook rows when a selector does not match.

## Pipeline statuses

- `EMPTY`: the source was downloaded but no configured indicator matched.
- `SKIPPED`: an optional authenticated source cannot run because credentials are absent.
- `FAILED`: an actual fetch, parse, validation or persistence error occurred.

These distinctions make scheduled-run monitoring actionable and prevent a zero-observation source from appearing healthy.

## 0.1.2 - SEPE PDF/XLS link disambiguation

The SEPE unemployment page publishes the PDF and XLS versions in the same HTML row. The
previous `html_excel` selector evaluated the complete row text when checking file types. As a
result, the PDF anchor was accepted because the adjacent XLS anchor contributed the text
`xls` to the same row context.

Version 0.1.2 checks the actual URL path extension using `urlparse` and only accepts `.xls`,
`.xlsx`, or `.csv` when those extensions are configured. Portals with genuinely opaque
file URLs can explicitly set `allow_opaque_links: true`.
