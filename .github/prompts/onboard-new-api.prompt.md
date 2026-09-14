---
description: Onboard a macroeconomic data source as a standalone collector repository.
agent: agent
---

# Onboard a New Source

Read `MASTER_MACRO_COLLECTOR_GUIDELINES.md` and `.github/skills/build-collector/SKILL.md` completely. The master guideline is authoritative when an older skill example conflicts with it.

Create one self-contained repository named `collector_<source>_<dataset>`. Do not create a shared framework.

Before generating files, require:

- SOURCE
- DATASET
- SOURCE_NAME
- RELEASE_NAME
- COUNTRY/currency code
- DOCS_URL
- TARGET_SERIES with native IDs
- INTENDED_FREQUENCY
- AUTH
- COLLECTOR_KIND

If series are not curated, run the series-selection workflow. Before writing `extract.py`, retrieve current official API documentation; never infer endpoints from model memory.

Follow all build phases. Verify source fidelity, database behavior, two-run idempotency, failure logging, security, and the full applicable Phase 8 checklist. Do not declare done with unchecked mandatory items.

If working from this hub, update `intake/collector_demands.csv` with the repository URL, status, owner, and next action.
