# EX-CPI compliance record

Executed 2026-09-14. Every row is `PASS`, `FAIL`, or `SKIP — reason`. No evidence
is reused from `collector_ons_cpi`; every figure below comes from this
collector's own runs. Databricks execution remains `SKIP`, so this collector is
**not** "100% production-certified".

The gates below were executed both locally against PostgreSQL 16 and in GitHub
Actions. Certification run
[#26](https://github.com/lucasweber1202/collector_ons_ex_cpi/actions/runs/34875804596)
is green on all four jobs: `quality`, `live-ons`, `historical-mm23` and
`postgresql`.

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
| pytest | PASS | 84 passed, 3 skipped (`python -m pytest -q -W ignore::DeprecationWarning`) |
| ruff check | PASS | `ruff check .` — all checks passed on ruff 0.16.7 |
| ruff format | PASS | `ruff format --check .` — 30 files already formatted (Python only; see the fleet-verbatim note under Template comparison) |
| mypy | PASS | `python -m mypy` — 30 source files, no issues |
| compileall | PASS | `python -m compileall -q main.py scripts tests` |
| Pipeline lookback regression | PASS | `tests/test_main_collect.py` drives `_collect()` through the transaction and asserts the 12-month YoY lookback is validation context only and is never persisted |

## Template comparison

**SKIP — `guimasuko/collector_template` is not reachable from this session.**
Re-tried 2026-09-14; every path failed: `add_repo` refuses the attachment
(`cross-tier adds are not supported in v1`), the GitHub API tool answers
`Access denied`, an anonymous `git clone` is refused with
`could not read Username`, `api.github.com` returns 403 through the proxy, and
`raw.githubusercontent.com/.../GUIDELINES.md` returns 404. The root
`GUIDELINES.md` and `FORECAST_TARGET_GUIDELINES.md` are not present in the
governance repository either, so those authorities are equally unavailable.

The comparison was therefore executed against the reachable authority,
`lucasweber1202/Coletores/MASTER_MACRO_COLLECTOR_GUIDELINES.md`, which the fleet
declares the consolidated contract overriding stale examples.

| Area | Guideline | EX-CPI today | Classification |
|---|---|---|---|
| Repository/schema name | `collector_<source>_<dataset>` == `SCHEMA_NAME` | `collector_ons_ex_cpi` both | MATCH |
| Layout | `main.py` at root, flat `scripts/`, no `core`/`lib`/`utils` | matches | MATCH |
| Extra modules | split only when the source forces it | `special_aggregates.py`, `special_aggregate_rates.py`, `special_aggregate_vintages.py` | ACCEPTABLE LOCAL EXTENSION — three distinct MM23 contracts (panel parse, rate validation, archived vintages) |
| `metadata` DDL | 13 columns, PK `series_id` | identical | MATCH |
| `time_series` DDL | 5 columns, PK `(series_id, reference_date, vintage_date)` | identical | MATCH |
| `logs` DDL | identity `id`, bounded text | identical | MATCH |
| `original_weights` | official weights stored untransformed when derived weights exist | present, plus `weight_base_year`; keyed `(series_id, reference_date, vintage_date)` | REQUIRED SOURCE-SPECIFIC EXCEPTION — two MM23 regimes per year cannot share a series-only key |
| Operational `weights` | only when weights are transformed | absent; this collector derives none | MATCH |
| 64-bit float | dialect common subset | `DOUBLE PRECISION` / `DOUBLE` per dialect | REQUIRED SOURCE-SPECIFIC EXCEPTION |
| Series IDs | structured, uppercase, round-trippable, no opaque native id alone | `EXCPI_INDEX_NATIVE_<CDID>` / `EXCPI_WEIGHT_NATIVE_<CDID>` | MATCH |
| `metadata.country` | ISO 4217 currency code | `GBP` | MATCH — was `GBR`, fixed in this PR |
| Vintage semantics | first sight, same-day update, later revision as a new row | implemented for both `time_series` and `original_weights` | MATCH |
| Release monitoring | empty DB builds now, populated DB polls, timeout is success | implemented; `tests/test_release_polling.py` | MATCH |
| HTTP | one managed client, timeout, bounded retry, no arbitrary redirects | single `http_get` with host allowlist | MATCH |
| Dependencies | minimal, mirrored, no `requests`/`python-dotenv`/ORM | mirrored; `tests/test_dependencies.py` enforces | MATCH |
| Standalone | no shared core, no cross-repo import | verified below | MATCH |
| Fleet-verbatim files | `.github/` copied byte-for-byte from the pilot | `.github/skills/` and `.github/prompts/` identical to the governance repository | MATCH — was GUIDELINE DRIFT, fixed in this PR |
| CI workflow | not added unless explicitly requested | `ex-cpi-certification.yml` | ACCEPTABLE LOCAL EXTENSION — explicitly requested; it is the only way to execute the PostgreSQL and live-ONS gates reproducibly |

One GUIDELINE DRIFT was found and fixed while certifying: an earlier commit on
this branch ran `ruff format` over the repository, and ruff 0.16 formats Python
fenced inside Markdown, so it rewrote the fleet-wide VERBATIM
`.github/skills/test-driven-development/SKILL.md`. The file is restored to the
governance repository's byte-for-byte copy and `extend-exclude = ["*.md"]` now
scopes ruff to this repository's Python, so a formatter can never rewrite a
fleet file again.

No BLOCKER remains open against the reachable authority. The residual risk this
gate keeps open is that the pilot's concrete files may differ from the guideline
prose in ways the prose does not describe.

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

| Case | Status | Evidence |
|---|---|---|
| Empty database builds history without polling | PASS | `tests/test_release_polling.py` |
| Populated database polls for the next expected month | PASS | `tests/test_release_polling.py` |
| `--start-date` explicit backfill, no polling | PASS | `tests/test_release_polling.py` |
| `--no-watch` collects without polling | PASS | executed twice on PostgreSQL above |
| Timeout exits 0 with no writes | PASS | `tests/test_release_polling.py` |
| ETag unchanged / changed / header missing | PASS | `tests/test_release_polling.py` |

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

1. **Databricks execution** — SKIP, no approved workspace or credentials.
2. **Template comparison against `guimasuko/collector_template`** — SKIP, the
   repository is unreachable from this session by every path tried.
