# EX-CPI compliance record

## Scope

- 10 official Table 38 index series: EXCPI_INDEX_NATIVE_<CDID>.
- 20 official MM23 weight series when available: exclusion plus complement EXCPI_WEIGHT_NATIVE_<CDID>.
- 60 reviewed MM23 CDIDs across the index, rate and weight crosswalks.
- Table 38 levels are stored; published MM23 12-month rates are validation-only.

## Static gates

| Gate | Status | Evidence |
|---|---|---|
| Pipeline blocker regression | SKIP — not executed in this GitHub API-only session | tests/test_main_collect.py added; it exercises _collect() through transaction and asserts the lookback month is not persisted. |
| pytest | SKIP — no Python command runtime exposed | Required command: python -m pytest -q -W ignore::DeprecationWarning. |
| ruff check | SKIP — no Python command runtime exposed | Required command: ruff check . |
| ruff format | SKIP — no Python command runtime exposed | Required command: ruff format --check . |
| mypy | SKIP — no Python command runtime exposed | Required command: python -m mypy. |
| compileall | SKIP — no Python command runtime exposed | Required command: python -m compileall -q main.py scripts tests. |

## Live-source evidence

| Gate | Status | Evidence |
|---|---|---|
| ONS Table 38 and MM23 reconciliation | SKIP — live test not executed | Required command: ONS_LIVE_TEST=1 python -m pytest tests/test_live_source.py -q -s. Record 10 CDIDs, 60 reviewed CDIDs, and residual metrics after execution. |

## Historical snapshot matrix

| Year | January snapshot/version | Feb-Dec regime | Status |
|---|---|---|---|
| 2017–current | Not executed | Not executed | SKIP — connected ONS audit not executed |

The implementation fails rather than infers January from the February–December regime when a scheduled-March snapshot is missing or ambiguous.

## PostgreSQL evidence

| Gate | Status | Evidence |
|---|---|---|
| Fresh database run | SKIP — no PostgreSQL endpoint available to this session | Run python -m scripts.init_db then python main.py --no-watch. |
| Immediate unchanged rerun | SKIP — fresh run not executed | Verify zero data/metadata writes and one success log. |

## Idempotency

| Gate | Status | Evidence |
|---|---|---|
| Time-series and original-weight same-day/later-vintage behavior | SKIP — test suite not executed | Existing persistence tests cover unchanged, same-day repair, and later revision semantics. |

## Revision/vintage tests

| Gate | Status | Evidence |
|---|---|---|
| Time series | SKIP — test suite not executed | Persistence modules require execution. |
| Original weights | SKIP — test suite not executed | tests/test_original_persistence.py uses EXCPI_WEIGHT_NATIVE_A9FU. |

## Rollback

| Gate | Status | Evidence |
|---|---|---|
| Transaction rollback after time-series/original-weights writes | SKIP — not yet implemented/executed | Add a PostgreSQL-backed failure-injection regression before promotion. |

## Databricks

| Gate | Status | Evidence |
|---|---|---|
| Static SQL portability | SKIP — Spark SQL parser unavailable | Review and parser execution required. |
| Real Databricks DDL/load/revision/two-run | SKIP — no approved workspace/credentials | Do not treat as PASS without a connected approved workspace. |

## Source gaps

- Historical snapshot discovery and live reconciliation have not been executed in this session; no source conclusion is asserted.

## Remaining blockers

- Certification remains open until the required quality gates, live ONS audit, historical matrix, PostgreSQL first/second run, rollback regression, and Databricks static gate are executed.
