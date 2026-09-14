# EX-CPI compliance record

Executed 2026-09-14. Every row is `PASS`, `FAIL`, or `SKIP — reason`. No evidence
is reused from `collector_ons_cpi`; every figure below comes from this
collector's own runs. Databricks execution remains `SKIP`, so this collector is
**not** "100% production-certified".

The code gates below were executed both locally against PostgreSQL 16 and in GitHub
Actions. The final certification run for code commit
`caef87af64c67b1c3655b2b2cb966012f0f96221`,
[34876624364](https://github.com/lucasweber1202/collector_ons_ex_cpi/actions/runs/34876624364),
is green on all four jobs — `quality`, `live-ons`, `historical-mm23` and
`postgresql` — with every step executed, including the second run, the
second-run no-op assertion, the recorded counts and the metadata-scope
assertion, all of which the previous red run had skipped.

## Scope

- 10 official Table 38 index series, `EXCPI_INDEX_NATIVE_<CDID>`:
  `DK9V`, `DKC5`, `DKC6`, `DKC7`, `DKC8`, `DKC9`, `DKD2`, `DKD3`, `DKD4`, `DKD5`.
- 20 official MM23 weight series, `EXCPI_WEIGHT_NATIVE_<CDID>`: one exclusion and
  one complement weight per aggregate, in parts per 1,000.
- 60 reviewed MM23 CDIDs across the index, rate and weight crosswalks.
- Table 38 levels are stored in `time_series`; published MM23 12-month rates are
  **validation-only** and are never persisted as observations.
- No operational `weights` table, no W1, no consumption segments, no full COICOP.
- `collector_ons_cpi` explicitly handed ownership of these ten CDIDs to this
  repository; they are not collected there.

## Static gates

| Gate | Status | Evidence |
|---|---|---|
| pytest | PASS | 90 passed, 3 skipped (`python -m pytest -q -W ignore::DeprecationWarning`) |
| ruff check | PASS | `ruff check .` — all checks passed on ruff 0.16.7 |
| ruff format | PASS | `ruff format --check .` — 30 files already formatted (Python only; see the fleet-verbatim note under Template comparison) |
| mypy | PASS | `python -m mypy` — 30 source files, no issues |
| compileall | PASS | `python -m compileall -q main.py scripts tests` |
| Pipeline lookback regression | PASS | `tests/test_main_collect.py` drives `_collect()` through the transaction and asserts the 12-month YoY lookback is validation context only and is never persisted |

## Template comparison

**PASS — executed directly on 2026-09-14.** Compared this repository with
[`guimasuko/collector_template`](https://github.com/guimasuko/collector_template)
at tree `8e4613b36c2808a7de234934a81bb26f7a22d367`: root
`GUIDELINES.md` (blob `1bf3df07a9b81932d26571def6bf0e531b8c1464`),
`FORECAST_TARGET_GUIDELINES.md` (blob
`7ac0663c7825443e1009a18481c0b73b0184b1cd`), repository layout, fleet
skills and configuration structure.

| Area | Classification | Evidence |
|---|---|---|
| Repository/schema identity and flat layout | MATCH | `collector_ons_ex_cpi` equals `SCHEMA_NAME`; root `main.py`; flat `scripts/`; no shared core |
| Fleet-standard `metadata`, `time_series`, `logs` | MATCH | Standard columns and keys preserved |
| Structured IDs and metadata vocabulary | MATCH | Round-trippable `EXCPI_*_NATIVE_<CDID>`; `metadata.country = GBP` |
| Vintage and unchanged-rerun semantics | MATCH | Same-day update, later-day insert, historical baseline stamped on collection, zero-write unchanged rerun |
| Forecast-target validation | MATCH | Horizon/context rates are validation-only; stored index levels remain official Table 38 observations |
| `original_weights` extension | NECESSARY SOURCE-SPECIFIC EXTENSION | Untouched MM23 weights require January and February–December regimes plus weight vintages |
| Separate MM23 modules | NECESSARY SOURCE-SPECIFIC EXTENSION | Exact panel parsing, published-rate validation and archived snapshot selection are distinct source contracts |
| No operational `weights` table | NECESSARY SOURCE-SPECIFIC EXTENSION | No transformed operational share is produced; inventing one would violate source fidelity |
| Certification workflow | NECESSARY SOURCE-SPECIFIC EXTENSION | Explicitly requested reproducible live ONS, historical-MM23 and PostgreSQL gates |
| Minor drift | MATCH | None found |
| Blocker | MATCH | None found |

The comparison closes the former accessibility SKIP. It does not close the
separate Databricks execution gate.

## Live ONS

| Gate | Status | Evidence |
|---|---|---|
| `tests/test_live_source.py` | PASS | 10 Table 38 index CDIDs and the 60 reviewed MM23 CDIDs all resolve against the current source |
| `tests/test_live_history.py` | PASS | historical MM23 snapshot matrix below |
| Table 38 parse | PASS | 10 EX-CPI series across 463 months, 1988-01 to 2026-07 |

## Reconciliation metrics

Measured on the current live source.

| Check | Checks | Failures | Median absolute residual | Maximum absolute residual |
|---|---:|---:|---:|---:|
| Complement weights (exclusion + complement = 1000), per aggregate | 10 | 0 | 0.0000000000 | 0.0000000000 |
| Complement weights, per aggregate-year over the full panel | 310 | 0 | 0.000000 | 0.000000 |
| Published 12-month rates, per aggregate | 10 | 0 | 0.0185068528 pp | 0.0437387823 pp |
| Published 12-month rates, per aggregate-month over the full panel | 4,510 | 0 | 0.024719 pp | 0.051019 pp |

The 12-month rate residuals are rounding-scale: MM23 publishes rates to one
decimal place, so a reconstruction from stored index levels cannot agree more
closely than about 0.05 pp. No tolerance was changed in this pass.

## Historical MM23 snapshot matrix

| Year | January snapshot | Superseded | Reason | Feb-Dec regime | Status |
|---|---|---|---|---|---|
| 2017 | v19 | 2017-03-21 | scheduled; last of 2 March releases (v18, v19) | MM23 annual 2017 | PASS |
| 2018 | v31 | 2018-03-20 | scheduled | MM23 annual 2018 | PASS |
| 2019 | v44 | 2019-03-20 | scheduled | MM23 annual 2019 | PASS |
| 2020 | v56 | 2020-03-25 | scheduled | MM23 annual 2020 | PASS |
| 2021 | v68 | 2021-03-24 | scheduled | MM23 annual 2021 | PASS |
| 2022 | v81 | 2022-03-23 | scheduled | MM23 annual 2022 | PASS |
| 2023 | v93 | 2023-03-22 | scheduled | MM23 annual 2023 | PASS |
| 2024 | v105 | 2024-03-20 | scheduled | MM23 annual 2024 | PASS |
| 2025 | v117 | 2025-03-26 | scheduled | MM23 annual 2025 | PASS |
| 2026 | v130 | 2026-03-25 | scheduled | MM23 annual 2026 | PASS |

No SOURCE GAP: every double-update year from 2017 has a recoverable
scheduled-March snapshot. January is never copied from the February-December
regime, and a missing snapshot fails the run rather than being inferred.

**March 2017, resolved from the source.** ONS published MM23 twice that March,
on the 14th and the 21st, so a uniqueness rule rejected the year. The archived
CSVs settle it: the snapshots superseded on 14 and 21 March carry *identical*
2017 weights (`A9F5` = 978.0), while the version published on 21 March already
carries the February-December regime (`A9F5` = 977.0). The 21 March release is
therefore the one that changed the weights, and the version it superseded — the
**last** scheduled March snapshot — is the January regime. The selector now
takes the last scheduled March snapshot, which is unchanged for every
single-release year and correct for 2017. A same-day correction is still never
a selector.

## PostgreSQL fresh build

PostgreSQL 16, empty database, `python -m scripts.init_db` then
`python main.py --no-watch`.

| Measure | Value |
|---|---|
| Status | PASS |
| `metadata` rows | 10 |
| `time_series` rows | 4,630 (10 series x 463 months) |
| `original_weights` rows | 7,440 (20 CDIDs x 372 months) |
| `logs` rows | 1 success on the first run |
| First reference_date | 1988-01-01 |
| Last reference_date | 2026-07-01 |
| Distinct `time_series` vintages | 1 |
| Distinct weight identities | 20 |
| Weight reference range | 1996-01-01 to 2026-12-01 |
| Metadata scope | exactly the 10 `EXCPI_INDEX_NATIVE_*` series |
| `metadata.country` | `GBP` for all 10 rows |

## Second run / idempotency

| Gate | Status | Evidence |
|---|---|---|
| Immediate unchanged rerun | PASS | `observations=0 vintages=0 official_weights=0 weight_vintages=0 metadata_inserted=0 metadata_updated=0` |
| Log row | PASS | one additional `success` row (2 total); no data table changed |

## Revisions / vintages

Executed against the populated PostgreSQL database.

| Case | Time series | Original weights |
|---|---|---|
| Unchanged rerun | 0 writes | 0 writes |
| Same-day change | 1 same-day update, 0 new rows, still 1 vintage | 1 same-day update, 0 new rows |
| Later-day revision | 1 new vintage row (4,631 rows, 2 vintages); prior vintage preserved unchanged | 1 new vintage row (7,441 rows, 2 vintages) |

The superseded observation stayed readable at its original vintage
(`2026-07-01` @ `2026-09-11` = 146.343) alongside the corrected one
(`2026-07-01` @ `2026-09-14` = 139.343). No historical vintage is overwritten.

## Rollback

| Gate | Status | Evidence |
|---|---|---|
| Transaction rollback after intermediate writes | PASS | `tests/test_postgresql_transaction.py` executed against real PostgreSQL: a failure injected after the time-series and original-weight writes leaves 0 partial `time_series` rows, 0 partial `original_weights` rows and no metadata row, and the run records its error log |

This supersedes the earlier statement that the rollback regression was
implemented but unexecuted.

## Release monitoring

Nine cases in `tests/test_release_polling.py`. Each routing assertion was
confirmed to fail against a deliberately broken orchestrator before being kept.

| Case | Status | Evidence |
|---|---|---|
| Empty database builds history without polling | PASS | `test_an_empty_database_builds_history_without_polling` — the watch loop and `time.sleep` both raise if reached |
| Populated database without `--no-watch` enters the watch loop | PASS | `test_a_populated_database_without_no_watch_enters_the_watch_loop` |
| `--start-date` explicit backfill, no polling | PASS | `test_an_explicit_start_date_never_polls` |
| `--no-watch` rewinds without polling | PASS | `test_no_watch_on_a_populated_database_rewinds_without_polling`, plus two executed PostgreSQL runs above |
| Timeout exits 0 with no writes | PASS | `test_timeout_without_a_release_is_a_normal_outcome`; the run then returns 0 |
| ETag unchanged | PASS | `test_unchanged_workbook_downloads_once` — one download, then header requests |
| ETag changed | PASS | `test_changed_workbook_is_redownloaded` |
| ETag header missing | PASS | `test_missing_entity_tag_never_optimises_a_release_away` — never optimises a release away |
| Release detected | PASS | `test_new_release_returns_immediately` |

## Spark / Databricks SQL grammar

| Gate | Status | Evidence |
|---|---|---|
| Every emitted statement parses as Spark SQL | PASS | all 16 statements from `init_db`, `time_series`, `original_weights`, `metadata` and `run_logs` parsed by Spark 4.1.1's own parser |
| Negative control | PASS | a PostgreSQL `ON CONFLICT` statement is rejected by the same parser, so a gate that stopped checking would fail |

## Databricks execution

**SKIP — no approved Databricks workspace/credentials.** No
`DBX_SERVER_HOSTNAME`, `DBX_HTTP_PATH`, `DATABRICKS_TOKEN` or `AKV_VAULT_URL` is
available and no workspace host is reachable. Unity Catalog paths, Delta
behaviour, permissions and real `MERGE` row semantics remain unverified. This is
never recorded as PASS.

## Source gaps

- **Special-aggregate weights before 1996.** MM23 carries annual weight rows from
  1988, but the `A9xx` special-aggregate weight series begin in 1996; 1988-1995
  expose only three of the twenty reviewed CDIDs (`CHZS`, `CHZU`, `CJWP`). Those
  months have index levels and no weight regime. The boundary is derived from
  the panel rather than hardcoded, so an ONS backfill moves it automatically,
  and a hole *after* the boundary raises rather than being skipped.

## Remaining blockers

1. **Databricks execution — SKIP.** No approved Databricks workspace, host or
   credentials are available. Spark SQL grammar passes, but no real Unity
   Catalog DDL/MERGE, two-run or revision execution was performed.

The template comparison is PASS and no code/data blocker is known. This
collector remains `verification`, not `ready`, solely because the mandatory
Databricks runtime gate is unexecuted.
