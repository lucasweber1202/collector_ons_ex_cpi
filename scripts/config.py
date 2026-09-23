"""Runtime settings loaded from environment variables and an optional .env."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
_ENV_FILE = ROOT_DIR / ".env"

if _ENV_FILE.exists():
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if value and key not in os.environ:
            os.environ[key] = value

SCHEMA_NAME = "collector_ons_ex_cpi"
CATALOG_NAME = "macrobond_inhouse"
METADATA_TABLE = "metadata"
TIME_SERIES_TABLE = "time_series"
ORIGINAL_WEIGHTS_TABLE = "original_weights"
WEIGHTS_TABLE = "weights"
LOGS_TABLE = "logs"

START_DATE_LOOKBACK_MONTHS = 5
PROD = os.getenv("PROD", "false").lower() in ("1", "true", "yes")
DATABASE_URL = os.getenv("COLLECTOR_DB_URL", "")
DEFAULT_START_DATE = date.fromisoformat(os.getenv("COLLECTOR_START_DATE", "1988-01-01"))

REQUEST_TIMEOUT = float(os.getenv("COLLECTOR_HTTP_TIMEOUT", "60"))
DOWNLOAD_DELAY = float(os.getenv("COLLECTOR_DOWNLOAD_DELAY", "2"))
MAX_RETRIES = int(os.getenv("COLLECTOR_MAX_RETRIES", "3"))
BACKOFF_FACTOR = float(os.getenv("COLLECTOR_BACKOFF_FACTOR", "2"))
# ONS throttles repeated file downloads per request. A throttled attempt needs a
# pause on the order of its enforcement window, not the transport-error backoff,
# and every wait stays bounded by MAX_RETRY_DELAY.
RATE_LIMIT_BACKOFF = float(os.getenv("COLLECTOR_RATE_LIMIT_BACKOFF", "20"))
MAX_RETRY_DELAY = float(os.getenv("COLLECTOR_MAX_RETRY_DELAY", "120"))
# The detailed reference workbook is the largest published artifact at a few
# megabytes; this ceiling is generous for it and still bounds memory.
MAX_DOWNLOAD_BYTES = int(os.getenv("COLLECTOR_MAX_DOWNLOAD_BYTES", str(128 * 1024 * 1024)))
USER_AGENT = os.getenv(
    "COLLECTOR_USER_AGENT",
    "collector_ons_ex_cpi/0.1 (+https://github.com/lucasweber1202/collector_ons_ex_cpi)",
)
LOG_LEVEL = os.getenv("COLLECTOR_LOG_LEVEL", "INFO")
POLL_INTERVAL = float(os.getenv("COLLECTOR_POLL_INTERVAL", "30"))
MAX_WAIT = float(os.getenv("COLLECTOR_MAX_WAIT", "900"))
VALIDATION_TOLERANCE_PP = float(os.getenv("COLLECTOR_VALIDATION_TOLERANCE_PP", "0.10"))

DBX_SERVER_HOSTNAME = os.getenv("DBX_SERVER_HOSTNAME", "")
DBX_HTTP_PATH = os.getenv("DBX_HTTP_PATH", "")
AKV_VAULT_URL = os.getenv("AKV_VAULT_URL", "")
AKV_SECRET_NAME = os.getenv("AKV_SECRET_NAME", "databricks-token")


def missing_environment(prod: bool = PROD) -> list[str]:
    """Return every required environment variable that is unset, not just the first.

    Reported before any database or HTTP work so one run surfaces the complete
    list. The Databricks token is deliberately absent: it may also come from a
    notebook/job context, so its absence is a warning rather than a hard failure.
    """
    if not prod:
        return [] if DATABASE_URL else ["COLLECTOR_DB_URL"]
    required = {"DBX_SERVER_HOSTNAME": DBX_SERVER_HOSTNAME, "DBX_HTTP_PATH": DBX_HTTP_PATH}
    return sorted(name for name, value in required.items() if not value)


def unresolved_credentials(prod: bool = PROD) -> list[str]:
    """Return credential sources that are unset but may still resolve at runtime."""
    if prod and not os.getenv("DATABRICKS_TOKEN") and not AKV_VAULT_URL:
        return ["DATABRICKS_TOKEN", "AKV_VAULT_URL"]
    return []
