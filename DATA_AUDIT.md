# Data audit — collector_ons_ex_cpi

- Audit seed: `20260918`
- Source version: live capture on 2026-09-18
- Test result: **88 passed, 5 skipped**
- Execution: **PASS**
- Overall: **PARTIAL** — Dez agregados EX-CPI validados. Vintages históricos anteriores à primeira coleta não são reconstruíveis pelo arquivo atual.
- Output: 4,640 observations, 10 series, 1988-01-01 to 2026-08-01.
- Sample: 20; values matched: 20; failures: 0; not verifiable: 0.

## Observation evidence

| # | Series | Period | Collector | Official source | Unit/frequency evidence | Result |
|---:|---|---|---:|---:|---|---|
| 1 | `EXCPI_INDEX_NATIVE_DKC8` | 1988-01-01 | 48.347 | 48.347 | index 2015=100; monthly; Table 38!FB8 | **PASS** |
| 2 | `EXCPI_INDEX_NATIVE_DKC6` | 2026-08-01 | 139.705 | 139.705 | index 2015=100; monthly; Table 38!FA471 | **PASS** |
| 3 | `EXCPI_INDEX_NATIVE_DKC7` | 1988-11-01 | 52.662 | 52.662 | index 2015=100; monthly; Table 38!FE18 | **PASS** |
| 4 | `EXCPI_INDEX_NATIVE_DKC7` | 1995-05-01 | 70.036 | 70.036 | index 2015=100; monthly; Table 38!FE96 | **PASS** |
| 5 | `EXCPI_INDEX_NATIVE_DKC5` | 1994-01-01 | 66.982 | 66.982 | index 2015=100; monthly; Table 38!EZ80 | **PASS** |
| 6 | `EXCPI_INDEX_NATIVE_DKD2` | 1993-07-01 | 65.989 | 65.989 | index 2015=100; monthly; Table 38!FF74 | **PASS** |
| 7 | `EXCPI_INDEX_NATIVE_DKD2` | 2012-02-01 | 95.605 | 95.605 | index 2015=100; monthly; Table 38!FF297 | **PASS** |
| 8 | `EXCPI_INDEX_NATIVE_DKD2` | 2003-07-01 | 76.105 | 76.105 | index 2015=100; monthly; Table 38!FF194 | **PASS** |
| 9 | `EXCPI_INDEX_NATIVE_DKD5` | 2006-12-01 | 82.405 | 82.405 | index 2015=100; monthly; Table 38!FH235 | **PASS** |
| 10 | `EXCPI_INDEX_NATIVE_DKD4` | 2013-06-01 | 98.649 | 98.649 | index 2015=100; monthly; Table 38!FG313 | **PASS** |
| 11 | `EXCPI_INDEX_NATIVE_DKC8` | 2025-11-01 | 139.525 | 139.525 | index 2015=100; monthly; Table 38!FB462 | **PASS** |
| 12 | `EXCPI_INDEX_NATIVE_DK9V` | 2026-05-01 | 141.288 | 141.288 | index 2015=100; monthly; Table 38!FR468 | **PASS** |
| 13 | `EXCPI_INDEX_NATIVE_DKC9` | 2026-05-01 | 140.343 | 140.343 | index 2015=100; monthly; Table 38!FC468 | **PASS** |
| 14 | `EXCPI_INDEX_NATIVE_DKD3` | 2026-02-01 | 140.606 | 140.606 | index 2015=100; monthly; Table 38!FD465 | **PASS** |
| 15 | `EXCPI_INDEX_NATIVE_DKD3` | 2004-01-01 | 76.218 | 76.218 | index 2015=100; monthly; Table 38!FD200 | **PASS** |
| 16 | `EXCPI_INDEX_NATIVE_DKC8` | 2021-12-01 | 115.184 | 115.184 | index 2015=100; monthly; Table 38!FB415 | **PASS** |
| 17 | `EXCPI_INDEX_NATIVE_DKC9` | 2025-12-01 | 138.612 | 138.612 | index 2015=100; monthly; Table 38!FC463 | **PASS** |
| 18 | `EXCPI_INDEX_NATIVE_DKD4` | 2003-04-01 | 78.141 | 78.141 | index 2015=100; monthly; Table 38!FG191 | **PASS** |
| 19 | `EXCPI_INDEX_NATIVE_DKC8` | 2025-04-01 | 138.198 | 138.198 | index 2015=100; monthly; Table 38!FB455 | **PASS** |
| 20 | `EXCPI_INDEX_NATIVE_DKC5` | 2021-10-01 | 112.892 | 112.892 | index 2015=100; monthly; Table 38!EZ413 | **PASS** |

## Filtering and metadata

- `{"universe_native_columns":173,"selected":10}`

The audit read the captured official artifact independently of the collector parser. It checked identifier linkage, published labels, units, frequency and first/latest boundaries. Source artifacts are identified by SHA-256 in the audit evidence.

## Point-in-time and revisions

Predictor as-of queries filter availability before ranking vintages. `inferred` and `unknown` remain excluded by default. Current mutable-file backfills are recorded at `first_seen`; later observed revisions create later vintages and do not inherit an original release timestamp. Actual pre-collection historical editions remain `NOT_VERIFIABLE` unless an archived source file exists.

## Corrections

- No source-semantic bug was found in this repository during the sample.

## Result

**PARTIAL** — Dez agregados EX-CPI validados. Vintages históricos anteriores à primeira coleta não são reconstruíveis pelo arquivo atual.
