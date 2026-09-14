"""Persist official ONS basket weights with the same vintage semantics as observations."""

from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Any

from sqlalchemy import TextClause, text
from sqlalchemy.engine import Connection

from scripts.config import ORIGINAL_WEIGHTS_TABLE, SCHEMA_NAME

logger = logging.getLogger(__name__)
_TABLE = f"{SCHEMA_NAME}.{ORIGINAL_WEIGHTS_TABLE}"
BATCH_SIZE = 500
ROUND_DECIMALS = 10

_COLUMNS = (
    "series_id",
    "reference_date",
    "vintage_date",
    "weight",
    "collected_at",
    "weight_base_year",
)
_KEY_COLUMNS = ("series_id", "reference_date", "vintage_date")
_UPDATE_COLUMNS = ("weight", "collected_at", "weight_base_year")
# See scripts/time_series.py: MERGE keeps a batch of same-day revisions in one
# statement; the fallback is only reached by the SQLite engine used in tests.
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
    logger.info("Weights %s: writing %d rows in batches of %d", operation, len(rows), BATCH_SIZE)
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        if merge:
            conn.execute(_merge_statement(len(batch)), _batch_parameters(batch))
        elif operation == "insert":
            conn.execute(_insert_statement(len(batch)), _batch_parameters(batch))
        else:
            conn.execute(_UPDATE_SQL, batch)
        logger.info(
            "Weights %s progress: %d/%d rows",
            operation,
            min(start + BATCH_SIZE, len(rows)),
            len(rows),
        )


_LATEST_SQL = text(
    f"""SELECT series_id, reference_date, vintage_date, weight, weight_base_year, collected_at
    FROM (SELECT series_id, reference_date, vintage_date, weight, weight_base_year, collected_at,
    ROW_NUMBER() OVER (PARTITION BY series_id, reference_date
    ORDER BY vintage_date DESC, collected_at DESC) AS rn
    FROM {_TABLE} WHERE reference_date >= :minimum_date) ranked WHERE rn = 1"""
)


def _latest(conn: Connection, minimum_date: date) -> dict[tuple[str, date], dict[str, Any]]:
    """Fetch the latest stored vintage per series and month."""
    rows = conn.execute(_LATEST_SQL, {"minimum_date": minimum_date}).mappings().all()
    result: dict[tuple[str, date], dict[str, Any]] = {}
    for row in rows:
        ref = (
            row["reference_date"].date()
            if isinstance(row["reference_date"], datetime)
            else row["reference_date"]
        )
        result[(str(row["series_id"]), ref)] = dict(row)
    return result


def upsert_original_weights(
    conn: Connection,
    rows: list[dict[str, Any]],
    collected_at: datetime,
) -> tuple[int, int]:
    """Write official weights idempotently and return ``(new, new_vintages)``.

    Each row states its own ``weight_base_year``. The two ONS weight products
    run different annual regimes -- W1 labels January and February-December of
    one calendar year, while a consumption-segment basket runs February to the
    following January -- so the regime year cannot be inferred from the
    reference month here.
    """
    today = collected_at.date()
    incoming: list[dict[str, Any]] = []
    for row in rows:
        weight = float(row["weight"])
        if math.isfinite(weight):
            incoming.append(
                {
                    "series_id": row["series_id"],
                    "reference_date": row["reference_date"],
                    "weight": weight,
                    "collected_at": collected_at,
                    "weight_base_year": int(row["weight_base_year"]),
                }
            )
    logger.info("Weights upsert: evaluating %d incoming weights", len(incoming))
    if not incoming:
        logger.info("No weight rows to upsert")
        return 0, 0
    existing = _latest(conn, min(row["reference_date"] for row in incoming))
    inserts: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    new_rows = 0
    new_vintages = 0
    for row in incoming:
        key = (row["series_id"], row["reference_date"])
        current = existing.get(key)
        if current is None:
            inserts.append({**row, "vintage_date": today})
            new_rows += 1
            continue
        # The published regime year is part of the official record, so a
        # corrected regime is a real change even when the value is unchanged.
        if (
            round(float(current["weight"]), ROUND_DECIMALS) == round(row["weight"], ROUND_DECIMALS)
            and int(current["weight_base_year"]) == row["weight_base_year"]
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
        "Weights upsert: new=%d new_vintages=%d same_day_updates=%d",
        new_rows,
        new_vintages,
        len(updates),
    )
    return new_rows, new_vintages
