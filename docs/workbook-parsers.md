# Official workbook parsers

Version 0.1.3 replaces generic label matching for the most irregular official workbooks.

## Social Security pension series (`SYYYYMM.xlsx`)

Connector: `social_security_pensions`

Sheet `S_Total` is a monthly time series with hierarchical headers. The parser selects the latest populated row in the absolute-value section and maps:

- `pension_count` -> Total pensions / Number
- `average_pension` -> Total pensions / Average pension
- `average_retirement_pension` -> Retirement / Average pension

The lower percentage-change table is explicitly ignored.

## Pension payroll (`ICONCEPTOSYYYYMM.xlsx`)

Connector: `social_security_pensions`

Sheet `Importe`, row `Total sistema`, first field under the `Total pensiones` group:

- `pension_monthly_payroll`

The source unit is millions of euros.

## Pensioners (`PTASYYYYMM.xlsx`)

Connector: `social_security_pensions`

Sheet `Resumen de datos`, row `Número de pensionistas`, column `AMBOS SEXOS`:

- `pensioner_count`

## SEPE registered unemployment (`evolparo.xls`)

Connector: `sepe_registered_unemployment`

The official legacy workbook has malformed OLE metadata and is rejected by `xlrd`. The connector therefore:

1. downloads the official `.xls`;
2. converts it to `.xlsx` using LibreOffice in headless mode;
3. finds the latest year block;
4. selects the latest month with a populated registered-unemployment value.

LibreOffice must be available as `libreoffice` or `soffice` on `PATH`.
