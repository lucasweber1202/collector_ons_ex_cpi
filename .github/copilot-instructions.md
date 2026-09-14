# Collector instructions

This repository is the self-contained forecast-target collector for ONS UK CPI exclusion aggregates. The fleet contract in `lucasweber1202/Coletores` is authoritative.

- Keep the standardized `metadata`, `time_series`, and `logs` tables.
- `original_weights` is the documented source-specific extension for untouched MM23 weights and vintages.
- Do not create an operational `weights` layer unless a real approved transformation is introduced.
- Store exactly the ten Table 38 index levels identified by native CDID.
- MM23 published 12-month rates and complement indices are validation-only.
- Preserve both official exclusion and complement weight CDIDs in `original_weights`.
- Identity is `EXCPI_INDEX_NATIVE_<CDID>`; titles never enter IDs.
- All matching is exact on official ONS identifiers. Fuzzy matching is forbidden.
- Missing or duplicated required CDIDs and unexpected source layouts fail loudly.
- From 2017, January and February–December weight regimes must be sourced separately.
- The repository must not import code or read a schema from `collector_ons_cpi`.
- Preserve idempotent same-day updates and later revision vintages.
- Do not report a quality gate as PASS unless executed on this repository.
