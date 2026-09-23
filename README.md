# collector_ons_ex_cpi

Standalone monthly collector for ten UK CPI exclusion (special aggregate) indices published by the Office for National Statistics.

## Stored series

| Measure | ONS CDID | series_id |
|---|---:|---|
| CPI excluding tobacco | DK9V | EXCPI_INDEX_NATIVE_DK9V |
| CPI excluding energy | DKC5 | EXCPI_INDEX_NATIVE_DKC5 |
| Core CPI (excluding energy, food, alcohol and tobacco) | DKC6 | EXCPI_INDEX_NATIVE_DKC6 |
| CPI excluding energy and unprocessed food | DKC7 | EXCPI_INDEX_NATIVE_DKC7 |
| CPI excluding seasonal food | DKC8 | EXCPI_INDEX_NATIVE_DKC8 |
| CPI excluding energy and seasonal food | DKC9 | EXCPI_INDEX_NATIVE_DKC9 |
| CPI excluding alcohol and tobacco | DKD2 | EXCPI_INDEX_NATIVE_DKD2 |
| CPI excluding liquid fuels, vehicle fuels and lubricants | DKD3 | EXCPI_INDEX_NATIVE_DKD3 |
| CPI excluding housing, water, electricity, gas and other fuels | DKD4 | EXCPI_INDEX_NATIVE_DKD4 |
| CPI excluding education, health and social protection | DKD5 | EXCPI_INDEX_NATIVE_DKD5 |

The ten forecast targets are official Table 38 index levels. MM23 headline and complement index levels are also stored in `time_series` as supporting reconstruction series, with their own metadata. Published 12-month rates remain validation-only. The published MM23 basket is stored unchanged in `original_weights`; normalized December-linked Young shares are stored in `weights`. Both weight tables use the index component's `series_id`. `weight_component_crosswalk` retains the native weight CDID, aggregate, component, role and source.

## Sources

- ONS CPI detailed reference tables, Table 38: index levels.
- ONS MM23 current and previous versions: exact CDID crosswalk, weights, complements, published 12-month rates, and January regimes.

No runtime import, database read, or schema dependency on `collector_ons_cpi` exists.

## Run

```bash
python -m pip install -r requirements.txt
export COLLECTOR_DB_URL=postgresql+psycopg2://...
python main.py --no-watch
```

Without `--no-watch`, a populated database polls for the next monthly release. An empty database performs the historical build. Revision lookback is configurable through the environment.

From 2017 onward January uses the archived December-reference regime; February–December uses the updated January-reference regime. Missing archive evidence fails the build and is reported as a source gap; the collector never copies February weights into January.

## Verification

```bash
python -m pytest -q -W ignore::DeprecationWarning
ruff check .
ruff format --check .
python -m mypy
python -m compileall -q main.py scripts tests
ONS_LIVE_TEST=1 python -m pytest tests/test_live_source.py -q -s
```

PostgreSQL, Databricks and live-source gates must be reported as skipped when their required environment is unavailable.
