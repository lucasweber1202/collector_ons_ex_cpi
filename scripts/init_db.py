"""Create the collector schema, standardized tables, and official weights table."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from scripts.config import LOGS_TABLE, METADATA_TABLE, SCHEMA_NAME, TIME_SERIES_TABLE, WEIGHTS_TABLE
from scripts.db import build_engine

# PostgreSQL and Databricks SQL share no spelling for a 64-bit float. Spark's
# parser lists DOUBLE as the only alias for DoubleType, so Databricks rejects
# DOUBLE PRECISION; PostgreSQL has no DOUBLE and rejects it in turn. FLOAT is
# not a way out: PostgreSQL resolves a bare FLOAT to 8-byte float8 while
# Databricks resolves it to 4-byte FloatType, which would silently halve stored
# precision instead of failing loudly. The spelling is therefore selected per
# dialect, the same way scripts/metadata.py selects MERGE.
DOUBLE_TYPES = {"postgresql": "DOUBLE PRECISION"}
DEFAULT_DOUBLE_TYPE = "DOUBLE"

CREATE_SCHEMA = f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_NAME}"
CREATE_METADATA_TABLE = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.{METADATA_TABLE} (
    series_id VARCHAR(200) NOT NULL,
    name VARCHAR(500) NOT NULL,
    description VARCHAR(2000),
    country VARCHAR(3) NOT NULL,
    frequency VARCHAR(20),
    unit VARCHAR(50),
    first_observation DATE,
    last_observation DATE,
    observation_count INTEGER NOT NULL,
    eco_group VARCHAR(250),
    source_url VARCHAR(1000) NOT NULL,
    last_publish_date DATE,
    collected_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_metadata PRIMARY KEY (series_id)
)
"""
CREATE_TIME_SERIES_TABLE = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.{TIME_SERIES_TABLE} (
    series_id VARCHAR(200) NOT NULL,
    reference_date DATE NOT NULL,
    vintage_date DATE NOT NULL,
    value {{double}} NOT NULL,
    collected_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_time_series PRIMARY KEY (series_id, reference_date, vintage_date)
)
"""
CREATE_WEIGHTS_TABLE = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.{WEIGHTS_TABLE} (
    series_id VARCHAR(200) NOT NULL,
    reference_date DATE NOT NULL,
    vintage_date DATE NOT NULL,
    weight {{double}} NOT NULL,
    collected_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_weights PRIMARY KEY (series_id, reference_date, vintage_date)
)
"""
CREATE_LOGS_TABLE = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.{LOGS_TABLE} (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP NOT NULL,
    status VARCHAR(20) NOT NULL,
    log_text VARCHAR(65535) NOT NULL,
    traceback VARCHAR(65535),
    CONSTRAINT pk_logs PRIMARY KEY (id)
)
"""

# UK-only, user-approved: retain monthly regimes and publication vintages.
CREATE_ORIGINAL_WEIGHTS_TABLE = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.original_weights (
    series_id VARCHAR(200) NOT NULL,
    reference_date DATE NOT NULL,
    vintage_date DATE NOT NULL,
    weight {{double}} NOT NULL,
    weight_base_year INTEGER NOT NULL,
    collected_at TIMESTAMP NOT NULL,
    CONSTRAINT pk_original_weights PRIMARY KEY (series_id, reference_date, vintage_date)
)
"""


def double_type(dialect: str) -> str:
    """Return the 64-bit float spelling this SQL dialect accepts."""
    return DOUBLE_TYPES.get(dialect, DEFAULT_DOUBLE_TYPE)


def init_db(engine: Engine) -> None:
    """Create all database objects idempotently."""
    double = double_type(engine.dialect.name)
    with engine.begin() as conn:
        for statement in (
            CREATE_SCHEMA,
            CREATE_METADATA_TABLE,
            CREATE_TIME_SERIES_TABLE.format(double=double),
            CREATE_WEIGHTS_TABLE.format(double=double),
            CREATE_ORIGINAL_WEIGHTS_TABLE.format(double=double),
            CREATE_LOGS_TABLE,
        ):
            conn.execute(text(statement))


if __name__ == "__main__":
    init_db(build_engine())
