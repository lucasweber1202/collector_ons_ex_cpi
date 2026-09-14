# UK CPI ex-CPI / special aggregates mapping

Status: the first exclusion set is mapped end-to-end **in memory**. The collector can read current MM23, discover archived January-regime snapshots, build the two official annual weight regimes, map them to the existing Table 38 `ALT` series and preserve the native MM23 weight CDIDs for eventual `original_weights` storage. No production persistence is enabled yet.

## Repository separation

The ONS publishes the exclusion measures inside the CPI statistical family, but this implementation is maintained in `collector_ons_ex_cpi` so that the EX-CPI pipeline can evolve independently from the headline CPI collector. The shared base still ingests Table 38 analytical aggregates as `CPI_ALT_*` index-level series. MM23 adds the native relationship between each exclusion aggregate's weight, index and 12-month rate.

## Authoritative sources

- Table 38, Consumer price inflation detailed reference tables: stored monthly `ALT` index levels.
- MM23, Consumer price inflation time series: source for special-aggregate weights, related index CDIDs, published 12-month rates and archived dataset vintages.
- W1-CPI: source for the standard COICOP basket hierarchy; MM23 does not replace it.
- ONS higher-level aggregation methodology and annual weights releases: source for the CPI double-weight regime above consumption-segment level.

ONS uses two higher-level CPI/CPIH weight updates from 2017 onward: December-reference weights for January aggregation and January-reference weights for February through December.

## Reviewed exclusion crosswalk

| Exclusion measure | MM23 weight | Monthly index | Published 12m rate | Removed component weight | Removed component index | Removed component 12m rate |
| --- | --- | --- | --- | --- | --- | --- |
| CPI excluding tobacco | `A9F5` | `DK9V` | `DKL7` | `CJWP` | `D7CB` | `D7GN` |
| CPI excluding energy | `A9FT` | `DKC5` | `DKO7` | `A9F3` | `DK9T` | `DKL5` |
| CPI excluding energy, food, alcohol and tobacco (core) | `A9FU` | `DKC6` | `DKO8` | `A9G4` | `DKD6` | `DKP8` |
| CPI excluding energy and unprocessed food | `A9FV` | `DKC7` | `DKO9` | `A9G5` | `DKD7` | `DKP9` |
| CPI excluding seasonal food | `A9FW` | `DKC8` | `DKP2` | `A9EZ` | `DK9R` | `DKL3` |
| CPI excluding energy and seasonal food | `A9FX` | `DKC9` | `DKP3` | `A9G6` | `DKD8` | `DKQ2` |
| CPI excluding alcohol and tobacco | `A9FY` | `DKD2` | `DKP4` | `CHZS` | `D7BV` | `D7G9` |
| CPI excluding liquid fuels, vehicle fuels and lubricants | `A9FZ` | `DKD3` | `DKP5` | `A9FS` | `DKC4` | `DKO6` |
| CPI excluding housing, water, electricity, gas and other fuels | `A9G2` | `DKD4` | `DKP6` | `CHZU` | `D7BX` | `D7GB` |
| CPI excluding education, health and social protection | `A9G3` | `DKD5` | `DKP7` | `A9G7` | `DKD9` | `DKQ3` |

All joins are exact on native ONS CDIDs. Names are descriptive only and are never fuzzy mapping keys.

## Implemented

### 1. Table 38 -> MM23 identity

`scripts/special_aggregates.py::resolve_table38_alt_series()` resolves each reviewed exclusion index CDID onto an already-collected Table 38 `ALT` series. It accepts only `family == ALT`, rejects duplicate CDIDs and reports missing targets rather than inventing a fallback.

### 2. MM23 current-file parser

`parse_mm23_special_aggregates()` reads the official wide MM23 CSV and retains exactly the 60 reviewed native series needed by this layer: 10 exclusion weights/indices/12-month rates plus the 10 corresponding removed-component triples. It interprets `YYYY` as annual rows, `YYYY MON` as monthly rows, ignores unrelated frequencies and fails if a reviewed CDID disappears.

### 3. Complement-weight validation

`complement_weight_checks()` checks the source-published exclusion and removed-component weights directly against 1,000 parts per 1,000. For the current 2026 core regime:

```text
A9FU = 794.8781
A9G4 = 205.1219
sum  = 1000.0000
```

### 4. Published 12-month-rate validation

`scripts/special_aggregate_rates.py::published_12m_rate_checks()` calculates:

```text
100 * (I[t] / I[t-12] - 1)
```

from the stored Table 38 `ALT` level and compares it with the corresponding MM23 published rate. MM23 rates are validation-only; they are not duplicated into `time_series`.

### 5. Archived January-regime discovery

`scripts/special_aggregate_vintages.py` parses the official MM23 previous-version table and selects, for each double-update year, the full-MM23 version **superseded by the scheduled March release**. Same-day correction rows are not used as selectors. For 2026 this resolves to `previous/v130/mm23.csv`, superseded on 25 March 2026 at 07:00.

### 6. Source-backed monthly weight regimes

`build_exclusion_weight_regimes()` implements the source semantics without fabrication:

```text
pre-2017:
    one published annual regime -> January through December

2017 onward, historical year or current year after the March release:
    archived February-release MM23 snapshot -> January only
    later/current MM23 annual value          -> February through December

current year while only the February release exists:
    current MM23 annual value -> January only
    February-December         -> not created yet
```

A historical double-update year without the archived January snapshot raises. The later February-December value is never silently copied into January.

### 7. Source identity and audit mapping

The existing `original_weights` convention preserves the identifier of the **weight source**, not merely the related index series. W1 already does this with `CPI_W1_*`. MM23 therefore follows the same design:

```text
CPI_MM23_A9FU  -> source weight CDID A9FU
                  mapped to Table 38 index CPI_ALT_..._DKC6
```

`build_mm23_original_weight_layer()` returns two structures ready for later integration:

- source-preserving monthly weights keyed as `CPI_MM23_<weight_cdid>`;
- an audit catalog containing the native weight CDID, official label, dataset/source and exact mapped `CPI_ALT_*` series.

`map_exclusion_weight_regimes_to_table38()` also exposes the target-series view for validation/joins, but that target ID is **not** the preferred persistence identity for official source weights.

## Weight-regime evidence

The current MM23 annual weight is not a generic twelve-month value. Official 2026 publications show two distinct regimes.

Core CPI:

```text
January 2026 regime:  CPI ex energy, food, alcohol and tobacco = 796.0030
Current/Feb-Dec MM23: A9FU                                    = 794.8781
```

Energy:

```text
January 2026 regime:  energy = 58.3320
Current/Feb-Dec MM23: A9F3   = 58.3510
```

This matches the ONS method: the first update is used only for January; the second update is introduced with the February index released in March.

## Persistence design

No database schema change is required. `original_weights` already carries:

```text
series_id, reference_date, vintage_date, weight, collected_at, weight_base_year
```

which is sufficient to distinguish the January and February-December monthly regimes. When integration is approved and verified, MM23 source IDs should be registered alongside the existing W1 source IDs and segment weights. `weights` must **not** be changed: the analytical `ALT` series remain overlapping standalone roots whose operational hierarchy weight is 1.0.

The audit workbook's existing `Original Weight Map` can carry the new `CPI_MM23_* -> CPI_ALT_*` crosswalk without a schema change.

## Live-source gate

The opt-in `ONS_LIVE_TEST=1` replay now exercises the new layer without persisting it. It checks:

- all 10 reviewed exclusion indices resolve to current Table 38 `ALT` rows;
- current MM23 complement-weight pairs reconcile;
- current Table 38 levels reconcile with MM23 published 12-month rates;
- the latest available archived January snapshot can be discovered and parsed;
- archived January complement weights reconcile;
- the current-year source-backed monthly regime can be constructed without inventing future months.

These assertions have been added to the test, but **this assistant environment has not executed the repository verification loop**. The PR remains draft for that reason.

## Remaining work before production persistence

1. Run unit tests, full pytest, mypy, ruff and the opt-in live-source replay in an environment with repository/network access.
2. During that replay, collect/verify every required scheduled-March snapshot from 2017 onward, not only the latest year, and record any source gaps.
3. Measure and document actual MM23 rate residuals and complement-weight residuals.
4. If those gates pass, call the existing `register_original_weights()` with the MM23 source-ID layer and audit catalog, then let the existing `original_weights` upsert persist it transactionally with the other data.
5. Add the MM23 validation summaries and source mapping to the audit workbook/`COMPLIANCE.md`, then re-run idempotency and rollback tests.

## Revision evidence

MM23 exposes previous dataset versions. ONS also published a correction on 18 February 2026 for special-aggregate CDIDs `KYHJ`, `KYHK`, `KYHL`, `KYHM` and `KYHQ` after double-linked indices had been used instead of single-linked indices for January 2026. Those rental aggregates are outside the ten exclusions above, but the correction is direct evidence that the special-aggregate layer must remain vintage-aware.
