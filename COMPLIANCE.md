# EX-CPI compliance record

This file deliberately contains no evidence copied from `collector_ons_cpi`. Counts for COICOP, W1, consumption segments, 697 series, 870 series or 90,064 observations are not evidence for this collector.

## Static scope review

- Target set: 10 official index CDIDs.
- Crosswalk: 60 reviewed MM23 CDIDs across six roles.
- Stored observations: Table 38 index levels only.
- Validation-only data: MM23 12-month rates and complement indices.
- Official weights: MM23 exclusion weights in parts per 1,000.
- Runtime dependency on `collector_ons_cpi`: none.
- Fuzzy matching: none.

## Evidence policy

A gate is PASS only when executed against this branch and this EX-CPI pipeline. The following must not be inferred from unit tests:

| Gate | Status until executed | Required evidence |
|---|---|---|
| pytest, ruff, format, mypy, compileall | NOT RUN | command output |
| live ONS | NOT RUN | ten CDIDs plus validation summary |
| PostgreSQL fresh DB | NOT RUN | first-run counts and second-run no-op |
| rollback | NOT RUN | failed transaction leaves no partial product rows |
| Databricks SQL | NOT RUN | portable SQL/Spark execution |
| historical snapshots | NOT RUN | year-by-year January and Feb-Dec matrix |

## Historical snapshot report

| Year | January snapshot | Feb-Dec regime | Status |
|---|---|---|---|
| 2017 onward | Must resolve to one scheduled-March previous version | Current/historical MM23 annual row | Pending connected audit |

No row may be changed to PASS without source evidence. A missing or ambiguous snapshot is a SOURCE GAP, never an inferred regime.
