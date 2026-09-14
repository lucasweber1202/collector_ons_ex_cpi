# Methodology

## Scope

This collector covers exactly ten national monthly UK CPI exclusion aggregates. Identity is the official ONS CDID, represented by the stable ID `EXCPI_INDEX_NATIVE_<CDID>`. Human-readable titles never participate in identity.

## Sources and selection

Table 38 supplies the stored index levels. Extraction reads only the ten reviewed index CDIDs and fails on a missing or duplicated required CDID. MM23 supplies six exact native identifiers per aggregate: exclusion weight, index, published 12-month rate, complement weight, complement index and complement rate. The reviewed crosswalk therefore contains 60 CDIDs and never uses fuzzy matching.

## Validation

For every available annual pair, exclusion weight plus complement weight must equal 1,000 within the official precision tolerance. For every reconcilable month, the collector calculates `100 * (I[t] / I[t-12] - 1)` and compares it with MM23's published rate. Logs report the number of checks and failures plus maximum and median absolute residual. An empty validation is a failure.

## Weight regimes

Official weights are stored unchanged in parts per 1,000. From 2017, January is sourced from the final MM23 version superseded by the scheduled March release; February–December uses the later annual value. A missing historical snapshot is a source gap and stops the requested backfill. Pre-2017 values are expanded only under the single-regime semantics exposed by MM23.

## Vintages and storage

`time_series` contains only official index levels. MM23 rates and complement indices are validation-only. `original_weights` contains the official exclusion-weight CDID, reference month, regime year, collection date and vintage date. No transformed `weights` table is required because this product performs no operational-weight transformation.

The primary key includes `vintage_date`. Same-day corrections update the same vintage; a later changed value creates a new vintage. Unchanged reruns produce no observation or weight rows and still produce a run log.

## Source gaps

The ONS previous-version page is the only accepted evidence for historical January regimes. Years without a unique scheduled-March snapshot are not marked complete. Live availability and the recovered-year matrix must be regenerated during a connected verification run.
