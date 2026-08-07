# Live-format validation — v0.1.4

This release was validated against the official workbook layouts used in July/June 2026.

## Corrections

- Added a dedicated parser for IGAE quarterly non-financial accounts (`T_AAPP.xlsx`).
  The previous generic parser could select unrelated rightmost cells and infer Q3 even when
  only Q1 was populated.
- Added a dedicated parser for IGAE State budget execution. It reads the correct current-year
  columns and converts the source's thousand-euro values to the catalog's million-euro unit.
- The Social Security affiliation article now chooses the latest explicit statistical month,
  rather than the first historical month mentioned in the prose.
- Dataset status is `partial` when only some expected indicators are extracted.
- DataComex is optional until credentials are configured; its absence no longer makes an
  otherwise healthy run fail.

## Verified observations

- IGAE general-government accounts, 2026-Q1:
  - revenue: 172,627 million EUR
  - expenditure: 179,057 million EUR
  - balance: -6,430 million EUR
- State budget execution, June 2026:
  - recognised revenue: 224,731.010 million EUR
  - revenue forecast: 192,544.168 million EUR
  - recognised expenditure: 187,829.911 million EUR
  - final appropriations: 458,104.069 million EUR
  - interest expenditure: 13,204.985 million EUR
- Social Security, July 2026:
  - average affiliations: 22,508,065
  - seasonally adjusted affiliations: 22,288,120
  - contributory pensions: 10,517,634
  - pensioners: 9,511,126
  - average pension: 1,372.1623 EUR/month
  - average retirement pension: 1,573.6516 EUR/month
  - monthly pension payroll: 14,431.9011 million EUR
- SEPE registered unemployment, July 2026: 2,311,499.

## Execution checks

The affected datasets were executed through `main.py` against a local HTTP replay server that
served the same HTML discovery patterns and the real official workbook bytes.

- First run: all seven selected datasets succeeded; 16 direct observations and 4 derived
  observations were inserted.
- Second run: all seven selected datasets succeeded; zero observations were inserted, proving
  idempotency.
- Test suite: 18 tests passed.

The execution environment used for packaging does not provide outbound DNS to run every live
HTTP call directly. INE, Banco de España and AEAT were therefore not re-fetched in this final
loop; their connectors had already succeeded in the user's prior live run and were not modified
except for shared status handling.
