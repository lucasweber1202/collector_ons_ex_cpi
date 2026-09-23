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

`time_series` contains the ten official Table 38 target levels plus MM23 headline and complement index levels labeled as supporting reconstruction inputs in `metadata`. MM23 published rates are validation-only. `original_weights` contains the ONS published basket points untouched, keyed by the component index's `series_id`, with reference month, regime year, collection date and vintage date. `weights` contains the normalized operational shares for the December-linked Young reconstruction, also keyed by component index ID. The top-level headline has an operational weight of 1.0. `weight_component_crosswalk` records target, component, role, native index CDID, native MM23 weight CDID and source. The preceding December index levels are persisted for the reconstruction base. Existing native-weight and native-share keys are re-keyed transactionally after collision preflight; values, vintages and timestamps are unchanged. The migration is idempotent and aborts ambiguous states.

The primary key includes `vintage_date`. Same-day corrections update the same vintage; a later changed value creates a new vintage. Unchanged reruns produce no observation or weight rows and still produce a run log.

## Source gaps

The ONS previous-version page is the only accepted evidence for historical January regimes. A year with no scheduled-March snapshot at all is a source gap and is not marked complete; a year with several is resolved by taking the last, which is the version the weight-changing release superseded. Live availability and the recovered-year matrix must be regenerated during a connected verification run.
