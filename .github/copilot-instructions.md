# Collector instructions

This is a self-contained forecast-target collector following the fleet contract
in `lucasweber1202/Coletores/MASTER_MACRO_COLLECTOR_GUIDELINES.md`. Read that
document and `.github/skills/` before changing anything here.

## Architecture

- Keep the repository flat and source-specific; do not add shared core classes,
  base collectors, ORM models, migrations, `requests`, or `python-dotenv`.
- Preserve the standardized `metadata`, `time_series`, and `logs` schemas.
- `scripts/segments.py` is the one justified extra module: ONS publishes
  consumption segments in a separate dataset, with a separate statistical
  status and a separate index reference. Do not fold it into `extract.py`, and
  do not add further modules without the same kind of justification.

## Weights: the two tables are not interchangeable

- `original_weights` stores the **official ONS weights exactly as published**,
  untransformed, in parts per 1,000: W1 basket rows (including weight-only
  subclasses that have no published index) and consumption-segment CPI weights.
  Each row carries the published regime year in `weight_base_year`.
- `weights` stores the **derived local operational shares** used directly to
  reconstruct a parent's monthly price relative. They are price-updated shares
  that sum to 1.0 within each parent; a standalone root carries 1.0.
- Never store points per 1,000 in `weights`, and never overwrite
  `original_weights` with a transformed value. `assert_operational_storage`
  exists to stop exactly that regression.

## Identifiers

- `series_id` is `CPI_{FAMILY}_{NODE}_{NATIVE_ID}` and carries only stable
  identifying fields. The official name lives in `metadata`, never in the
  identifier: an ONS title edit must not fork the stored history.
- Keep identifiers structured, uppercase and round-trippable through
  `parse_series_id()`.

## Behaviour

- Preserve idempotency: an unchanged second run writes no metadata, observation,
  weight, or original-weight rows, but does write one successful log row.
- Never invent an index level from a weight, and never invent a weight from an
  index. A node that exists only in the weights table stays weight-only.
- Validation runs before persistence and a failure stops the run. An empty or
  low-coverage validation is a failure, not a pass.
- Verify endpoints, workbook layouts and CSV layouts against current official
  ONS sources; the schema gates in `extract.py` and `segments.py` must fail
  loudly rather than trust a moved column.
- Every SQL statement the collector sends is listed in
  `tests/conftest.py::emitted_sql`. Add a new query there too, or it is checked
  by neither the portable-subset gate nor the Spark grammar gate.
- Run the verification loop in `.github/skills/verification-loop/SKILL.md`
  before every PR. It includes `python -m mypy`, which must stay clean.
