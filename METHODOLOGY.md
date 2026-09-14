# UK CPI collection and bottom-up methodology

The collector stores official published levels, never derived growth rates, and
stores every weight needed to rebuild a published aggregate from its children.
Three ONS products are read, each kept separate because each has its own
classification, index reference and statistical status.

## Sources and their statistical status

| Product | Content | Status as published by ONS |
| --- | --- | --- |
| Detailed reference tables, Table 38 | CPI index levels to three decimals, 2015=100, from January 1988 | "not accredited official statistics ... analytical purposes only" |
| Annex A table W1-CPI | official basket weights in parts per 1,000 from 2008 | accredited official statistics |
| Consumption segment indices | segment-level CPI index and CPI weight, monthly editions from February 2025 | research data, not accredited official statistics |

The banner text is captured during extraction and written into
`metadata.description` for every series, so no consumer has to infer which layer
a series came from. The two index layers never share a series identifier and are
never spliced into one level.

## Identifiers

`series_id` is `CPI_{FAMILY}_{NODE}_{NATIVE_ID}` and carries only fields that
identify the series:

```text
CPI_COICOP_ALL_D7BT     all items
CPI_COICOP_D01_D7BU     COICOP division 01
CPI_COICOP_G011_D7C8    COICOP group 01.1
CPI_COICOP_C0111_D7D5   COICOP class 01.1.1
CPI_ALT_A13_DK9T        ONS analytical aggregate "Energy"
CPI_CS_SEG_CP0111301    consumption segment "BREAD, WHITE"
```

The official name is deliberately absent. ONS edits series titles without
changing the series, and a title-derived identifier would fork the stored
history on a purely textual edit. Names, descriptions, classification codes and
source URLs live in `metadata`, sourced from the workbook or CSV each
observation came from. `parse_series_id()` round-trips the four parts.

The consumption-segment identifier carries the ONS `CS_ID` and not the segment's
classification, because the classification is what changes at a reclassification
while the segment itself continues.

## Hierarchy

Every series records its immediate parent during extraction, so the tree is a
read of the source structure rather than a second guess at it:

```text
ALL -> division (Dxx) -> group (Gxxx) -> class (Cxxxx) -> consumption segment (SEG)
```

- COICOP parents come from the published classification code in Table 38.
- Consumption-segment parents are resolved from the segment's official COICOP5
  subclass and COICOP4 class to the **deepest Table 38 node that actually
  publishes an index**. A class with no published index falls back to its group,
  then to its division.
- ONS merges some classes into one published index. The weights workbook is the
  only place that states the merge, so component classes are resolved through
  the workbook code: `07.3.6` resolves to the index published for `07.3.2/6`,
  `09.5.4` to `09.5.3/4`, and so on.
- Class `07.1.1` is published as two indices, `07.1.1A` new cars and `07.1.1B`
  second-hand cars, which the class code alone cannot separate. The segment's
  COICOP5 subclass does separate them, through a reviewed two-entry alias table.
- Nothing is matched on names. An ambiguous code raises rather than picking a
  best candidate, and an unresolvable code raises rather than attaching the
  segment to all items.
- Analytical `ALT` aggregates are stored but excluded from the COICOP tree,
  because they are overlapping cuts rather than one additive hierarchy.

The current source resolves 697 segments onto 85 distinct published parents.

## Classification regimes

Consumption-segment editions come in two published layouts and both are handled
explicitly:

- from the February 2026 edition the CSV carries `COICOP4_ID`/`COICOP5_ID`,
  which are authoritative for that month;
- earlier editions carry only `CS_ID`, so the classification is read from the
  ONS CPI classification framework file of the same classification year, at the
  month being classified (the framework carries `start_date`/`end_date`
  validity, and a segment reclassified mid-year must be read at its month).

A classification year runs February to the following January. A file that
matches neither layout, or a segment the framework does not classify, stops the
run instead of being guessed at.

## Weight regimes

W1 publishes weights in parts per 1,000. From 2017 onward, ONS uses two annual
regimes above consumption-segment level:

- the December-reference weights apply to January;
- the January-reference weights apply from February through December.

Pre-2017 annual weights apply to all twelve months of their year. The collector
expands each source regime to its applicable reference months and stores the
published absolute weights unchanged in `original_weights`.

Consumption-segment weights run on the **other** annual boundary: one basket
applies from February to the following January. `weight_base_year` therefore
records 2025 for a January 2026 segment weight and 2026 for a January 2026 W1
weight. The regime year is stored per row rather than inferred downstream.

## Index references

- Table 38 publishes 2015=100 levels that are never re-referenced to January.
- Consumption-segment indices are re-referenced every January.

That difference is not cosmetic; it changes which arithmetic is valid, and the
two layers are therefore reconciled with different formulas.

## Aggregation formulas

### COICOP aggregates from COICOP children

ONS aggregates with a Laspeyres-type index against an annual January price
reference and chains the series in December. Reconstructing a parent price
relative therefore requires price-updating the child weights to that reference
month. For parent `p` and children `i`:

```text
ref(t)     = January of year(t), except January itself, which chains on
             December of year(t-1)
phi(i,t)   = w(i,t) * I(i,t-1)/I(i,ref) /
             sum_j w(j,t) * I(j,t-1)/I(j,ref)
R_hat(p,t) = sum_i phi(i,t) * I(i,t)/I(i,t-1)
```

`R_hat(p,t)` is compared with `I(p,t)/I(p,t-1)`.

The `I(i,ref)` term is not optional. Dropping it is equivalent to assuming every
child shares one January index level. On a synthetic two-child parent built by
the ONS rule (weights 400/600, one child rising 3% per month, one flat) the
omission produces -0.0213 pp in March, -0.0860 pp in June and -0.1078 pp in
July, breaching the 0.10 pp tolerance on perfectly consistent data.
`tests/test_bottom_up_price_updated_weights.py` pins both the same-January and
different-January cases.

### Published parents from consumption segments

Within one chain year every segment index shares one January reference, so
`I(i,ref)` is a constant that cancels in the normalization and the price update
reduces to the previous month's level:

```text
phi(i,t)   = w(i,t) * I(i,t-1) / sum_j w(j,t) * I(j,t-1)
R_hat(p,t) = sum_i phi(i,t) * I(i,t)/I(i,t-1)
```

A link whose two months straddle the January re-reference is **not defined at
source**. Measured on the full published panel, every link with both months
outside January reconciles its Table 38 parent to a median absolute residual of
0.000273 pp, while the December-to-January link misses by a median 3.38 pp and
the January-to-February link by a median 0.63 pp. Those links are therefore
reported as unreconcilable and excluded from the coverage denominator, exactly
like pre-2008 months in the weights window. No operational share is derived for
a month whose link does not exist, so `weights` carries segment shares only from
March through December of each chain year.

## Worked reproductions from the stored tables

Both examples below use only rows the collector persists: `time_series`,
`weights` and the `parent_series_id` recorded in `metadata` and the Series Map.
No source file is needed to check them.

### A COICOP parent from its COICOP children

`CPI_COICOP_D01_D7BU` (01 Food and non-alcoholic beverages), July 2026. The
price reference is January 2026, and the two children are the published groups.

| Child | `weights` phi | I(Jun 2026) | I(Jul 2026) |
| --- | ---: | ---: | ---: |
| `CPI_COICOP_G011_D7C8` FOOD | 0.8866345377 | 143.166 | 143.151 |
| `CPI_COICOP_G012_D7C9` NON-ALCOHOLIC BEVERAGES | 0.1133654623 | 150.981 | 151.078 |

The stored shares already carry the price update, so they apply directly:

```text
R_hat = 0.8866345377 * 143.151/143.166 + 0.1133654623 * 151.078/150.981
      = 0.9999799375
published = 144.029/144.032 = 0.9999791713
residual  = +0.0000766 percentage points
```

Reproducing the shares from `original_weights` instead, with the January 2026
reference levels 143.254 and 149.471 and the W1 weights 97.2974 and 12.3085:

```text
phi_FOOD = 97.2974 * 143.166/143.254
         / (97.2974 * 143.166/143.254 + 12.3085 * 150.981/149.471)
         = 0.8866345377
```

### A published parent from its consumption segments

`CPI_COICOP_C0452_D7DU` (04.5.2 Gas), July 2026. Both months lie outside
January, so the link is defined and the stored shares apply directly.

| Segment | `original_weights` (pts/1000) | `weights` phi | I(Jun 2026) | I(Jul 2026) |
| --- | ---: | ---: | ---: | ---: |
| `CPI_CS_SEG_420301` GAS | 6.479 | 0.6157237353 | 94.615 | 116.254 |
| `CPI_CS_SEG_420302` GAS - FIXED TARIFF | 3.489 | 0.3505107961 | 100.019 | 101.473 |
| `CPI_CS_SEG_420404` BUTANE GAS | 0.333 | 0.0337654685 | 100.951 | 104.392 |

```text
R_hat     = 0.6157237353 * 116.254/94.615
          + 0.3505107961 * 101.473/100.019
          + 0.0337654685 * 104.392/100.951
          = 1.1470659776
published = 158.997/138.611 = 1.1470734646
residual  = -0.0007487 percentage points
```

The segment index levels are on the January 2026 reference and the parent is on
2015=100. Only the ratios are compared, so the two references never have to be
reconciled -- which is also why the same arithmetic is invalid across a January.

## Stored weights

- `original_weights` holds the official published value, untouched.
- `weights` holds the operational `phi` shares, which multiply monthly index
  relatives directly. Consumers must not price-update them a second time. The
  root and standalone analytical aggregates receive 1.0. Weights are only
  derived where the required price-reference indices exist; a missing reference
  produces no weight rather than an invented one.
- `assert_operational_storage` refuses to run against a database whose `weights`
  table still holds points per 1,000.

## Checks performed on every run

1. **W1 weight additivity** -- immediate-child absolute weights must sum to the
   parent weight, absolute tolerance 0.01 parts per 1,000.
2. **COICOP bottom-up rate** from official W1 weights, residual in percentage
   points, tolerance `COLLECTOR_VALIDATION_TOLERANCE_PP` (default 0.10 pp).
3. **COICOP bottom-up rate** from the stored operational shares, applied
   directly without normalization or a second price update.
4. **Segment weight coverage** -- published segment weights must never exceed
   their parent's basket weight beyond rounding, and must cover at least
   `COLLECTOR_MIN_SEGMENT_WEIGHT_RATIO` of it (default 0.90).
5. **Segment bottom-up rate** from official segment weights, tolerance
   `COLLECTOR_SEGMENT_TOLERANCE_PP` (default 0.50 pp).
6. **Segment bottom-up rate** from the stored operational shares.

Every check reports performed, passed, failed, skipped, coverage, median and
maximum absolute residual. A tolerance breach, an empty reconcilable layer and
coverage below `COLLECTOR_MIN_VALIDATION_COVERAGE` all stop the run before any
data is written. Skips whose cause is the source itself -- months before the
2008 weights window, and segment links across the January re-reference -- are
reported separately and excluded from the coverage denominator so that they can
neither mask a defect nor manufacture a failure.

## Measured residuals on the current published source

| Check | Performed | Failed | Coverage | Median absolute | Maximum absolute |
| --- | ---: | ---: | ---: | ---: | ---: |
| W1 immediate-child sums | 8,436 | 0 | 100% | 0.000000 | 0.000200 points per thousand |
| COICOP bottom-up, official weights | 8,251 | 0 | 100% | 0.000292 pp | 0.001980 pp |
| COICOP bottom-up, operational shares | 8,251 | 0 | 100% | 0.000292 pp | 0.001980 pp |
| Segment weight coverage | 1,530 | 0 | 100% | 0.000500 | 7.185400 points per thousand |
| Segment bottom-up, official weights | 1,275 | 0 | 100% | 0.000273 pp | 0.336575 pp |
| Segment bottom-up, operational shares | 1,275 | 0 | 100% | 0.000273 pp | 0.336575 pp |

A further 8,843 COICOP links fall before the published weights window and 170
segment links cross the January re-reference; both are unreconcilable at source.

The two segment maxima come from the same parent, `04.1 actual rentals for
housing`: ONS builds that published class partly from administrative rental
sources that are not published as consumption segments, so the segments cover
92.35% of its basket weight and reproduce its monthly relative to 0.34 pp rather
than to the 0.001 pp seen elsewhere. Every other parent reconciles inside
0.05 pp. The 0.50 pp segment tolerance and the 0.90 coverage floor are set to
admit exactly that documented gap and nothing wider.

Table 38 publishes three-decimal levels. The 0.10 pp COICOP tolerance is a
conservative historical configuration, not a claim about published rounding;
tightening it requires a separately agreed error budget across historical
regimes.

## Known limitations

- Consumption-segment history begins with the February 2025 edition. ONS
  published item indices, a different and deeper level, before that date; they
  are not stitched into the segment series.
- A consumption-segment series spans the January re-reference, so a
  month-on-month rate must not be computed across January from the stored
  levels. This is the source's own convention and is recorded per series in
  `metadata.description`.
- COICOP subclasses have official weights but no published index. Their weights
  are preserved in `original_weights`; no index, metadata row or operational
  weight is created for them.
- `04.1 actual rentals for housing` is only 92.35% covered by published
  segments, as above.
- The ONS analytical aggregates are overlapping cuts and are not reconciled
  bottom-up; they receive operational weight 1.0 as standalone roots.

## Audit workbook

`--export-validation` writes `_verify_xls/ons_cpi_validation.xlsx` from the
database after the run has written it, so it reports stored rows rather than
process memory. Sheets: Run (as-of vintage date, view and row counts),
Time Series, Weights, Original Weights, Series Map (identifier, family, level,
native id, official name, classification, parent, dataset, statistical status,
source URL, stored history), Original Weight Map (official code, CDID, label,
mapped series, dataset) and Validation. An analyst can reproduce any published
aggregate from Time Series, Weights and Series Map alone.

See [COMPLIANCE.md](COMPLIANCE.md) for approved UK-local schema exceptions,
migration safety and the executed verification evidence.
