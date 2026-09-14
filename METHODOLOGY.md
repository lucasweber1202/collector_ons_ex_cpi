# Methodology

## Scope

This collector covers exactly ten national monthly UK CPI exclusion aggregates. Identity is the official ONS CDID, represented by the stable ID `EXCPI_INDEX_NATIVE_<CDID>`. Human-readable titles never participate in identity.

## Sources and selection

Table 38 supplies the stored index levels. Extraction reads only the ten reviewed index CDIDs and fails on a missing or duplicated required CDID. MM23 supplies six exact native identifiers per aggregate: exclusion weight, index, published 12-month rate, complement weight, complement index and complement rate. The reviewed crosswalk therefore contains 60 CDIDs and never uses fuzzy matching.

## Validation

For every available annual pair, exclusion weight plus complement weight must equal 1,000 within the official precision tolerance. For every reconcilable month, the collector calculates `100 * (I[t] / I[t-12] - 1)` and compares it with MM23's published rate. Logs report the number of checks and failures plus maximum and median absolute residual. An empty validation is a failure.

## Weight regimes

Official weights are stored unchanged in parts per 1,000. From 2017, January is sourced from the final MM23 version superseded by the scheduled March release; February–December uses the later annual value. When ONS publishes MM23 more than once in March, as it did on 14 and 21 March 2017, the January regime is the **last** scheduled March snapshot: the version the weight-changing release superseded. A missing historical snapshot is a source gap and stops the requested backfill. Pre-2017 values are expanded only under the single-regime semantics exposed by MM23. The special-aggregate weight series themselves begin in 1996, so earlier months carry index levels and no weight regime; that boundary is derived from the published panel rather than hardcoded.

## Vintages and storage

`time_series` contains only official index levels. MM23 rates and complement indices are validation-only. `original_weights` contains the official exclusion-weight CDID, reference month, regime year, collection date and vintage date. No transformed `weights` table is required because this product performs no operational-weight transformation.

The primary key includes `vintage_date`. Same-day corrections update the same vintage; a later changed value creates a new vintage. Unchanged reruns produce no observation or weight rows and still produce a run log.

## Source gaps

The ONS previous-version page is the only accepted evidence for historical January regimes. A year with no scheduled-March snapshot at all is a source gap and is not marked complete; a year with several is resolved by taking the last, which is the version the weight-changing release superseded. Live availability and the recovered-year matrix must be regenerated during a connected verification run.
