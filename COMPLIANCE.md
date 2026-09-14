# UK CPI guideline compliance — evidence of the current state

Baseline: `lucasweber1202/Coletores/MASTER_MACRO_COLLECTOR_GUIDELINES.md`,
SHA-256 `59739074fd125161929dda70828d7876de548ceef110dddfa7c12ec38fc9c72e`,
verified against the live governance repository on 2026-09-11. Exceptions below
are UK-local; they do not redefine the fleet contract.

## Approved exceptions and exact storage semantics

- Standard `metadata`, `time_series` and `logs` schemas remain unchanged.
- PostgreSQL emits `DOUBLE PRECISION`; Databricks emits `DOUBLE` (both 64-bit).
  `FLOAT` is never emitted: it is 8 bytes on PostgreSQL and 4 on Databricks.
- `original_weights` retains `weight_base_year` and `collected_at`, adding
  `reference_date` and `vintage_date` with key
  `(series_id, reference_date, vintage_date)`. This is the user-approved
  regime-safe exception: a series-only key cannot preserve January versus
  February–December, the February-to-January consumption-segment basket, or
  revisions.
- `original_weights` stores official values without normalization: W1 rows in
  source points per thousand and consumption-segment `CPI_WEIGHT` in parts per
  thousand. `weight_base_year` is the **published regime year**, supplied per
  row because the two products run different annual boundaries — W1 labels
  January and February–December of one calendar year, while a segment basket
  runs February to the following January.
- W1 source identifiers encode the workbook code: `CPI_W1_01P1P1P1` means
  `01.1.1.1`; `P` encodes a dot and `S` a slash. A/B suffixes stay distinct.
  `ALLGOODS`, `ALLSERVICES` and `0` denote the source analytical totals and the
  overall index. Consumption-segment weights use the segment's own series
  identifier, so `original_weights` joins directly to `metadata` for that layer.
- `weights` stores local operational shares for each immediate parent. The root
  and standalone analytical aggregates receive 1.0. These shares multiply
  monthly index relatives directly; consumers must not price-update them again.
- The price-reference denominator documented in METHODOLOGY.md is the approved
  UK formula exception; it is required for Table 38's 2015=100 levels.
- `scripts/segments.py` is one extra source-specific module. It is justified by
  the source, not by taste: consumption segments come from a different ONS
  dataset, carry a different statistical status, use a different index
  reference, and arrive in two published layouts, one of which needs the CPI
  classification framework file. Folding it into `extract.py` would put three
  unrelated parsing contracts in one module and push it past 1,000 lines. No
  shared core, base class, ORM, migration framework or cross-repo import is
  introduced.

## Source scope and evidence (2026-09-11, publication stamp 19 August 2026)

Stored on a real PostgreSQL 16.13 database from a full historical build:

| Layer | Series | Observations | First | Last |
| --- | ---: | ---: | --- | --- |
| COICOP (all items, 12 divisions, 38 groups, 71 classes) | 122 | 54,516 | 1988-01-01 | 2026-07-01 |
| ONS analytical aggregates | 51 | 23,410 | 1988-01-01 | 2026-07-01 |
| Consumption segments | 697 | 12,138 | 2025-02-01 | 2026-07-01 |
| **Total** | **870** | **90,064** | | |

Weights:

| Table | Rows | Distinct series | First | Last |
| --- | ---: | ---: | --- | --- |
| `original_weights`, W1 codes | 56,436 | 319 | 2008-01-01 | 2026-12-01 |
| `original_weights`, segment weights | 12,138 | 697 | 2025-02-01 | 2026-07-01 |
| `weights`, COICOP operational shares | 27,206 | 122 | 2008-01-01 | 2026-07-01 |
| `weights`, analytical roots at 1.0 | 11,373 | 51 | 2008-01-01 | 2026-07-01 |
| `weights`, segment operational shares | 10,115 | 697 | 2025-03-01 | 2026-07-01 |

Segment shares start in March and skip every January and February because the
January re-reference leaves those links undefined at source; no share is
invented for a month whose link does not exist.

Only 122 of the 173 Table 38 series have mapped W1 COICOP basket weights; the
51 analytical aggregates are overlapping cuts with no basket row. 192 W1
subclass rows and the merged/total rows have official weights and no published
index: their weights are preserved and no index, metadata row or operational
weight is fabricated for them. 697 consumption segments resolve onto 85 distinct
published Table 38 parents.

### Reconciliation performed on the live source

| Check | Performed | Failed | Coverage | Median absolute | Maximum absolute |
| --- | ---: | ---: | ---: | ---: | ---: |
| W1 immediate-child sums | 8,436 | 0 | 100% | 0.000000 | 0.000200 points per thousand |
| COICOP bottom-up, official weights | 8,251 | 0 | 100% | 0.000292 pp | 0.001980 pp |
| COICOP bottom-up, operational shares | 8,251 | 0 | 100% | 0.000292 pp | 0.001980 pp |
| Segment weight coverage | 1,530 | 0 | 100% | 0.000500 | 7.185400 points per thousand |
| Segment bottom-up, official weights | 1,275 | 0 | 100% | 0.000273 pp | 0.336575 pp |
| Segment bottom-up, operational shares | 1,275 | 0 | 100% | 0.000273 pp | 0.336575 pp |

8,843 COICOP links fall before the 2008 weights window and 170 segment links
cross the January re-reference. Both are unreconcilable at source, are reported
separately, and are excluded from the coverage denominator. Every other skip
counts against coverage. Tolerance failures, an empty reconcilable layer and
coverage below the configured floor stop data persistence. The legacy
`--strict-validation` argument is still accepted and cannot disable any gate.

Both segment maxima come from `04.1 actual rentals for housing`, where ONS uses
administrative rental sources that are not published as consumption segments;
segment coverage of that parent is 92.35%. Every other parent reconciles inside
0.05 pp. Tolerances are set to admit exactly that documented gap.

### Mapping discipline

Mappings use official classification codes, then the reviewed W1-code-to-CDID
alias dictionary in `scripts/extract.py`, then the two-entry subclass alias in
`scripts/segments.py` that separates new from second-hand cars inside class
07.1.1. No fuzzy scoring exists anywhere. Ambiguous nodes, missing reviewed
CDIDs, conflicting values, unresolved hierarchy parents, unclassifiable segments
and conflicting classification-framework rows all raise. Agreeing duplicate
mappings are reported and the source rows stay separate. The audit workbook's
Original Weight Map records every official code, CDID, label and target.

## Identifier migration — required before deploying to an existing database

`series_id` no longer embeds the official series name. The previous spelling was
`CPI_COICOP_D01_D7BU_FOOD_AND_NON_ALCOHOLIC_BEVERAGES`; the current spelling is
`CPI_COICOP_D01_D7BU`. The change removes a real defect: an ONS title edit
previously forked a series' stored history into two identifiers.

`assert_current_series_ids` runs inside the write transaction, before any data
is written, and refuses to proceed when `metadata` or `time_series` still holds
the old spelling. Nothing is renamed, deleted or rewritten automatically.

A reviewed migration for an existing populated database, to be executed
deliberately and with a backup, is a rename in place:

```sql
BEGIN;
-- Inspect first: every old identifier maps to exactly one new one.
SELECT series_id,
       split_part(series_id, '_', 1) || '_' || split_part(series_id, '_', 2) || '_' ||
       split_part(series_id, '_', 3) || '_' || split_part(series_id, '_', 4) AS new_series_id
FROM collector_ons_ex_cpi.metadata
WHERE LENGTH(series_id) - LENGTH(REPLACE(series_id, '_', '')) > 3
ORDER BY series_id;

-- Then apply to every table that stores a series_id.
UPDATE collector_ons_ex_cpi.time_series      SET series_id = <new> WHERE series_id = <old>;
UPDATE collector_ons_ex_cpi.weights          SET series_id = <new> WHERE series_id = <old>;
UPDATE collector_ons_ex_cpi.original_weights SET series_id = <new> WHERE series_id = <old>;
UPDATE collector_ons_ex_cpi.metadata         SET series_id = <new> WHERE series_id = <old>;
COMMIT;
```

The rename preserves every vintage, so no history is lost and no observation is
re-collected. Two old identifiers can only collide on one new identifier if ONS
published two rows with the same family, node and CDID, which `parse_cpi_workbook`
already rejects; run the inspection query first to confirm. `original_weights`
rows keyed by `CPI_W1_*` are unaffected. **No existing production database has
been migrated by this change.**

## Database upgrade safety

Earlier versions stored official points per thousand in `weights`. The pipeline
checks for that legacy representation **inside the write transaction, before any
data write**, and refuses to mix it with local operational shares. It does not
overwrite or delete historical vintages.

Deployment to an existing populated database requires a reviewed archival and
full-history rebuild plan using its actual data and backups, plus the identifier
migration above. For verification, use a separate empty database with the
standard collector schema. Do not drop an existing production table merely to
pass a check.

## Partial-release protection

All four writes — `time_series`, `original_weights`, `weights`, `metadata` —
now run inside a single `engine.begin()` transaction, after both storage guards.
On PostgreSQL (and on the SQLite database used in tests) a failure anywhere in
that block rolls the whole release back;
`tests/test_pipeline_safety.py` injects a failure after the observation write
and after the weight write and asserts that all four tables stay empty.

Databricks SQL commits each statement separately, so cross-table atomicity is
not available there. The run logs an explicit warning naming the dialect, and an
interrupted release is repaired by the next run: every upsert is idempotent and
the revision rewind re-presents the same months. Metadata is still derived from
the post-write database state inside the same transaction, so it can never
describe a partially written history.

## Verification commands

```bash
pip install -e ".[dev]"
python -m pytest -q -W ignore::DeprecationWarning
ruff check .
ruff format --check .
python -m mypy
python -m compileall -q main.py scripts tests
ONS_LIVE_TEST=1 python -m pytest tests/test_live_source.py -q -W ignore::DeprecationWarning
DATABRICKS_SQL_PARSE_TEST=1 python -m pytest tests/test_databricks_sql_grammar.py -q
python -m scripts.init_db && python main.py --no-watch --export-validation
```

Useful inspection query for current values:

```sql
SELECT series_id, reference_date, value, vintage_date, collected_at
FROM (
    SELECT series_id, reference_date, value, vintage_date, collected_at,
           ROW_NUMBER() OVER (
               PARTITION BY series_id, reference_date
               ORDER BY vintage_date DESC, collected_at DESC
           ) AS rn
    FROM collector_ons_ex_cpi.time_series
) ranked
WHERE rn = 1;
```

## Executed verification

| Gate | Status | Evidence |
| --- | --- | --- |
| Build/import | PASS | `python -m compileall -q main.py scripts tests` |
| Type check | PASS | `python -m mypy` — 42 source files, no issues, under `disallow_untyped_defs`, `warn_unreachable`, `warn_unused_ignores`, `warn_redundant_casts`, `no_implicit_optional` |
| Ruff lint | PASS | `ruff check .` — all checks passed |
| Ruff format | PASS | `ruff format --check .` — all files already formatted |
| Tests | PASS | 331 passed, 3 skipped (the live-source and Spark-grammar tests are opt-in) |
| Live ONS source | PASS | `ONS_LIVE_TEST=1` replay: two identical runs then an injected failure; 90,064 observations, 48,694 operational weights, 68,574 original weights, 870 metadata rows; every series' first, middle and last published value compared against the parsed source |
| PostgreSQL 16.13 | PASS | `python -m scripts.init_db`, full live build, unchanged second run, forced same-day revisions exercising the MERGE path on all four tables, and an injected validation failure |
| Databricks execution | SKIP | no Databricks workspace or credentials are reachable from this environment |
| Databricks SQL grammar | PASS | every emitted statement parsed by Spark 4.1.1's own SQL parser (see below) |
| Security review | PASS | see below |
| Diff review | PASS | `git diff --stat` and full diff reviewed; no secret, `.env`, debug print, generated workbook or binary committed |

### PostgreSQL evidence

- DDL created five tables in `collector_ons_ex_cpi` with `double precision`,
  bounded `character varying`, `date`, `timestamp` and a `bigint` identity key
  on `logs`.
- Fresh build wrote 90,064 observations, 68,574 original weights, 48,694
  operational weights and 870 metadata rows.
- An immediate second run wrote **0** observations, 0 vintages, 0 operational
  weights, 0 original weights, 0 metadata inserts and 0 metadata updates, and
  appended one successful `logs` row.
- Corrupting one stored value per table and re-running produced exactly one
  same-day update per table, repaired every value and added no rows
  (`time_series` stayed at 90,064). This is the PostgreSQL `MERGE` path.
- A forced validation failure produced one `error` log with a traceback, left
  all four data tables byte-identical, and left `MAX(collected_at)` unchanged.
- `log_text` truncation is visible: run 1's log ends with
  `[..., truncated ...]` at 65,535 characters.
- Output sanity on the stored database: 870 metadata rows, 0 with
  `observation_count <= 0`, 0 null first/last observations, 0 duplicate
  `(series_id, reference_date, vintage_date)` triples, 0 null or non-finite
  values, 0 operational weights outside `[0, 1]`, one country, one frequency,
  one unit and one eco_group.

### Databricks SQL: parsed by the engine's own grammar

`tests/conftest.py::emitted_sql` is the single inventory of every statement the
collector sends — 31 of them, covering the six DDL statements and every insert,
merge, update, guard and read. Two gates run over that one inventory, so a query
added to the collector cannot be reviewed by neither.

1. `tests/test_init_db_portability.py` asserts that no statement uses `SERIAL`,
   `JSONB`, `ON CONFLICT`, `RETURNING`, `ILIKE`, `DISTINCT ON`, `FILTER (`, a
   `::` cast, `DOUBLE PRECISION`, `TIMESTAMPTZ`, `NOW()` or
   `CURRENT_TIMESTAMP`; that the only inlined literals are the reviewed set
   `{'CPI%', '_', ''}`, so no value stopped travelling as a bound parameter;
   that every statement addresses only the collector schema; and that `MERGE`
   matches on the full natural key rather than relying on a primary key,
   because Databricks treats key constraints as informational.
2. `tests/test_databricks_sql_grammar.py` (opt-in, about eight seconds) parses
   all 31 statements with **Spark 4.1.1's own SQL parser**, reached through the
   `pyspark` dependency the collector already declares for token resolution.
   Databricks SQL is Spark SQL, so this replaces a reviewer's reading of the
   SQL with the engine's verdict on it. All 31 parse, including
   `BIGINT GENERATED ALWAYS AS IDENTITY`, the informational `PRIMARY KEY`
   constraints, the `MERGE ... USING (SELECT ... UNION ALL ...)` shape and the
   named `:parameter` markers. The suite includes a negative control — a
   PostgreSQL `ON CONFLICT` statement that the parser must reject — so a gate
   that silently stopped checking anything would fail.

What this does **not** prove: Unity Catalog semantics, Delta table behaviour,
permissions, or that a MERGE produces the intended rows on a real warehouse.
Those still require the Databricks execution gate below, which remains SKIP.

### Security review

- No hardcoded secret, credential or token in code, `.env.example`, tests or
  documentation; `.env` is gitignored and untracked.
- No `eval`, `exec`, `pickle`, `yaml.load`, `os.system` or `subprocess` anywhere.
- Every SQL value travels as a named parameter. The only interpolated
  identifiers are the module-level schema and table constants from
  `scripts/config.py`; no identifier is ever built from source data or user
  input.
- Every request goes through `_http_get`, which refuses any host outside
  `{www.ons.gov.uk, ons.gov.uk}`. URLs discovered in ONS pages are resolved with
  `urljoin` and then pass that allowlist, so a hostile absolute link in the page
  cannot redirect the collector elsewhere. Redirects are disabled
  (`follow_redirects=False`) and TLS verification is left on.
- Bounded timeout, bounded retries, `Retry-After` support, a rate-limit backoff
  and a maximum download size are all configured, and the body is read in chunks
  so an oversized response is refused before it is fully buffered.
- Schema gates validate every workbook and CSV layout before any value is
  trusted; an ONS rate-limit notice returned with HTTP 200 fails the gate rather
  than being parsed.
- The Databricks token is never logged; `scripts/db.py` renders the database URL
  with `hide_password=True`.
- Database work uses context-managed connections and one transaction per release.
- No `print` outside a `__main__` block; no production debug output.
- No CRITICAL or HIGH finding remains open.

## Tests

`tests/` is justified by this collector's parsing, hierarchy, vintage, weight,
mathematical-transformation, release-polling and forecast-target validation
logic. 331 committed tests cover: stable identifiers and the legacy-identifier
guard; deterministic weights-workbook selection across years, orders, absence
and rename; Table 38, W1 and consumption-segment schema drift; consumption
segment classification in both published layouts, the classification framework's
per-month validity, combined-class and split-class resolution, ambiguity and
unresolvable codes; index-without-weight and weight-without-index; the January
chain link and the February–December regime; ground-truth bottom-up
reconstruction for both layers; operational weight reproduction; original-weight
preservation and regime years; same-day and later-day vintages; two-run
idempotency; mid-persistence failure rollback; release polling; the audit
workbook including as-of vintage selection and the empty-database case; SQL
portability and the Spark grammar; HTTP retry, rate limiting and download
bounds; engine construction and credential redaction; run-log truncation and
best-effort persistence; every spelling pandas uses for an empty workbook cell;
and the declared dependency surface.

The two worked reproductions in METHODOLOGY.md are pinned by
`tests/test_documented_examples.py`: it feeds the documented inputs through the
shipped functions and asserts the documented shares and residuals, and checks
that the published residual table still reports zero failures. A formula change
therefore cannot leave the documentation quietly wrong.

## Remaining gates

1. **Databricks execution.** Execute the DDL, MERGE, two-run and failure tests
   in an approved Databricks workspace. Not reachable from this environment;
   marked SKIP above, never PASS. The grammar of every emitted statement is
   now validated by Spark's own parser, which narrows the risk to runtime and
   catalog semantics rather than syntax.
2. **Live pilot comparison.** `guimasuko/collector_template` could not be
   reached from this session (cross-owner attachment is unsupported here and an
   anonymous clone is refused), and the only other fleet repository,
   `lucasweber1202/collector_rosstat_cpi`, currently contains a README only.
   `.github/prompts/`, `.github/skills/` and `.vscode/` were therefore
   reconciled byte-for-byte against the fleet governance repository
   `lucasweber1202/Coletores`, and `.gitignore` against the same file plus two
   marked collector-local entries (`_verify_xls/`, `*.egg-info/`). Re-run the
   comparison against the template repository when access exists.
3. **Migration against real pre-existing vintages.** The identifier migration
   and the legacy-weights rebuild above are documented and guarded but have not
   been executed against a real populated production database.
4. **Consumption-segment history before February 2025.** ONS published item
   indices, a deeper and differently classified level, before the consumption
   segment product began. They are deliberately not stitched into the segment
   series. Extending coverage backwards is a separate, scoped decision.
5. **Intake status.** `lucasweber1202/Coletores/intake/collector_demands.csv`
   is updated to `verification` with the remaining gates as its next action in
   `lucasweber1202/Coletores` PR #5; it is `verification` rather than `ready`
   precisely because gate 1 above is unexecuted.

Sources:
- [ONS CPI detailed tables](https://www.ons.gov.uk/economy/inflationandpriceindices/datasets/consumerpriceinflation)
- [ONS W1–W3 weights](https://www.ons.gov.uk/economy/inflationandpriceindices/datasets/consumerpriceinflationupdatingweightsannexatablesw1tow3)
- [ONS item/consumption-segment indices and classification frameworks](https://www.ons.gov.uk/economy/inflationandpriceindices/datasets/consumerpriceindicescpiandretailpricesindexrpiitemindicesandpricequotes)
- [ONS higher-level aggregation](https://www.ons.gov.uk/economy/inflationandpriceindices/methodologies/higherlevelaggregationandweightsinconsumerprices)
