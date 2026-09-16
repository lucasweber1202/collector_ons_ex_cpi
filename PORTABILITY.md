# Standalone portability

Verified 2026-09-16. `collector_ons_ex_cpi` is operationally standalone and
does not import or inspect `collector_ons_cpi`. Its ONS contracts are versioned
inside this repository.

```powershell
git clone https://github.com/lucasweber1202/collector_ons_ex_cpi.git
Set-Location collector_ons_ex_cpi
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m pytest -q
ruff check .
mypy .
python main.py --once
```

Supported Python: 3.11 and 3.12. PostgreSQL via `COLLECTOR_DB_URL` is required
for local collection. Databricks is optional behind `PROD=true` and requires
its documented DBX/token or Key Vault settings only when selected. Imports are
configuration-free.

Network: HTTPS to `www.ons.gov.uk`/`ons.gov.uk`, plus the configured PostgreSQL
or optional Databricks/Azure endpoints. TLS is never disabled; use standard
`SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` for a corporate CA.

Certification: standalone code PASS; sibling required NO; absolute developer
path NO; Databricks required NO; external database required for collection YES.
