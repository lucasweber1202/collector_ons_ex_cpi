"""Idempotent time-series persistence with collection-date vintage tracking."""

from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Any

from sqlalchemy import TextClause, bindparam, text
from sqlalchemy.engine import Connection, Engine

from scripts.config import SCHEMA_NAME, TIME_SERIES_TABLE

logger = logging.getLogger(__name__)
_TABLE = f"{SCHEMA_NAME}.{TIME_SERIES_TABLE}"
BATCH_SIZE = 500
SERIES_BATCH_SIZE = 50
ROUND_DECIMALS = 10

_COLUMNS = ("series_id", "reference_date", "vintage_date", "value", "collected_at")
_KEY_COLUMNS = ("series_id", "reference_date", "vintage_date")
_UPDATE_COLUMNS = ("value", "collected_at")
# Dialects whose MERGE lets a whole batch of same-day revisions travel in one
# statement. Everything else falls back to a parameter-sequence UPDATE, which is
# only reached by the in-process SQLite engine used in tests.
_MERGE_DIALECTS = frozenset({"databricks", "postgresql"})


def _batch_parameters(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Flatten a batch into named parameters suffixed by row position."""
    return {
        f"{column}_{index}": row[column] for index, row in enumerate(rows) for column in _COLUMNS
    }


def _insert_statement(count: int) -> TextClause:
    """Build one multi-row INSERT covering ``count`` rows."""
    values = ", ".join(
        "(" + ", ".join(f":{column}_{index}" for column in _COLUMNS) + ")" for index in range(count)
    )
    return text(f"INSERT INTO {_TABLE} ({', '.join(_COLUMNS)}) VALUES {values}")


def _merge_statement(count: int) -> TextClause:
    """Build one Databricks-compatible MERGE covering ``count`` rows."""
    source = " UNION ALL ".join(
        "SELECT " + ", ".join(f":{column}_{index} AS {column}" for column in _COLUMNS)
        for index in range(count)
    )
    condition = " AND ".join(f"target.{column} = source.{column}" for column in _KEY_COLUMNS)
    assignments = ", ".join(f"{column} = source.{column}" for column in _UPDATE_COLUMNS)
    return text(
        f"MERGE INTO {_TABLE} AS target USING ({source}) AS source ON {condition} "
        f"WHEN MATCHED THEN UPDATE SET {assignments}"
    )


_UPDATE_SQL = text(
    f"UPDATE {_TABLE} SET {', '.join(f'{column}=:{column}' for column in _UPDATE_COLUMNS)} "
    f"WHERE {' AND '.join(f'{column}=:{column}' for column in _KEY_COLUMNS)}"
)


def _write_batches(
    conn: Connection, rows: list[dict[str, Any]], operation: str, merge: bool
) -> None:
    """Apply rows in bounded statements, logging INFO progress per batch."""
    if not rows:
        return
    logger.info(
        "Time-series %s: writing %d rows in batches of %d", operation, len(rows), BATCH_SIZE
    )
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        if merge:
            conn.execute(_merge_statement(len(batch)), _batch_parameters(batch))
        elif operation == "insert":
            conn.execute(_insert_statement(len(batch)), _batch_parameters(batch))
        else:
            conn.execute(_UPDATE_SQL, batch)
        logger.info(
            "Time-series %s progress: %d/%d rows",
            operation,
            min(start + BATCH_SIZE, len(rows)),
            len(rows),
        )


_MAX_REFERENCE_SQL = text(f"SELECT MAX(reference_date) FROM {_TABLE}")
_AGGREGATES_SQL = text(
    f"""SELECT series_id, MIN(reference_date) AS first_observation,
    MAX(reference_date) AS last_observation,
    COUNT(DISTINCT reference_date) AS observation_count,
    MAX(collected_at) AS last_collected_at
    FROM {_TABLE} GROUP BY series_id"""
)
_LATEST_SQL = text(
    f"""SELECT series_id, reference_date, vintage_date, value, collected_at
    FROM (SELECT series_id, reference_date, vintage_date, value, collected_at,
    ROW_NUMBER() OVER (PARTITION BY series_id, reference_date
    ORDER BY vintage_date DESC, collected_at DESC) AS rn
    FROM {_TABLE} WHERE reference_date >= :minimum_date AND series_id IN :series_ids) ranked
    WHERE rn = 1"""
).bindparams(bindparam("series_ids", expanding=True))


def get_max_reference_date(engine: Engine) -> date | None:
    """Return the latest stored reference month."""
    with engine.connect() as conn:
        value = conn.execute(_MAX_REFERENCE_SQL).scalar()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value)
    return value


def get_series_aggregates(conn: Connection) -> dict[str, dict[str, Any]]:
    """Return per-series first, last, distinct count, and latest collection."""
    rows = conn.execute(_AGGREGATES_SQL).mappings().all()
    return {str(row["series_id"]): dict(row) for row in rows}


def _incoming_rows(
    parsed_by_date: dict[date, dict[str, float | None]], collected_at: datetime
) -> list[dict[str, Any]]:
    """Flatten finite observations into database-shaped rows."""
    rows: list[dict[str, Any]] = []
    for reference_date, values in parsed_by_date.items():
        for series_id, raw_value in values.items():
            if raw_value is None:
                continue
            value = float(raw_value)
            if not math.isfinite(value):
                continue
            rows.append(
                {
                    "series_id": series_id,
                    "reference_date": reference_date,
                    "value": value,
                    "collected_at": collected_at,
                }
            )
    return rows


def _latest(
    conn: Connection, series_ids: list[str], minimum_date: date
) -> dict[tuple[str, date], dict[str, Any]]:
    """Fetch latest vintages for a bounded series batch."""
    rows = (
        conn.execute(_LATEST_SQL, {"minimum_date": minimum_date, "series_ids": series_ids})
        .mappings()
        .all()
    )
    result: dict[tuple[str, date], dict[str, Any]] = {}
    for row in rows:
        ref = (
            row["reference_date"].date()
            if isinstance(row["reference_date"], datetime)
            else row["reference_date"]
        )
        result[(str(row["series_id"]), ref)] = dict(row)
    return result


def upsert_time_series(
    conn: Connection,
    parsed_by_date: dict[date, dict[str, float | None]],
    collected_at: datetime,
) -> tuple[int, int]:
    """Write new observations/revisions and return ``(new, new_vintages)``."""
    today = collected_at.date()
    incoming = _incoming_rows(parsed_by_date, collected_at)
    logger.info("Time-series upsert: evaluating %d incoming observations", len(incoming))
    if not incoming:
        logger.info("No time-series rows to upsert")
        return 0, 0
    by_series: dict[str, list[dict[str, Any]]] = {}
    for row in incoming:
        by_series.setdefault(row["series_id"], []).append(row)
    inserts: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    new_observations = 0
    new_vintages = 0
    series_ids = sorted(by_series)
    minimum_date = min(row["reference_date"] for row in incoming)
    for start in range(0, len(series_ids), SERIES_BATCH_SIZE):
        batch_ids = series_ids[start : start + SERIES_BATCH_SIZE]
        existing = _latest(conn, batch_ids, minimum_date)
        for series_id in batch_ids:
            for row in by_series[series_id]:
                current = existing.get((series_id, row["reference_date"]))
                if current is None:
                    inserts.append({**row, "vintage_date": today})
                    new_observations += 1
                    continue
                if round(float(current["value"]), ROUND_DECIMALS) == round(
                    row["value"], ROUND_DECIMALS
                ):
                    continue
                vintage = (
                    current["vintage_date"].date()
                    if isinstance(current["vintage_date"], datetime)
                    else current["vintage_date"]
                )
                if vintage == today:
                    updates.append({**row, "vintage_date": today})
                else:
                    inserts.append({**row, "vintage_date": today})
                    new_vintages += 1

    merge = conn.dialect.name in _MERGE_DIALECTS
    _write_batches(conn, inserts, "insert", merge=False)
    _write_batches(conn, updates, "same-day update", merge=merge)
    logger.info(
        "Time-series upsert: new=%d new_vintages=%d same_day_updates=%d",
        new_observations,
        new_vintages,
        len(updates),
    )
    return new_observations, new_vintages
