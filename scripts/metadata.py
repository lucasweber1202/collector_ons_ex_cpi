"""Build and idempotently upsert standardized metadata after observation writes."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import TextClause, text
from sqlalchemy.engine import Connection

from scripts.config import METADATA_TABLE, SCHEMA_NAME, TIME_SERIES_TABLE
from scripts.extract import (
    COUNTRY_CURRENCY,
    ECO_GROUPS,
    FREQUENCIES,
    UNITS,
    get_last_publish_date,
    parse_series_id,
)
from scripts.time_series import get_series_aggregates

logger = logging.getLogger(__name__)
_TABLE = f"{SCHEMA_NAME}.{METADATA_TABLE}"
_TIME_SERIES = f"{SCHEMA_NAME}.{TIME_SERIES_TABLE}"
BATCH_SIZE = 500
_COMPARABLE_COLUMNS = (
    "name",
    "description",
    "country",
    "frequency",
    "unit",
    "first_observation",
    "last_observation",
    "observation_count",
    "eco_group",
    "source_url",
    "last_publish_date",
)
_COLUMNS = ("series_id", *_COMPARABLE_COLUMNS, "collected_at")
_UPDATE_COLUMNS = tuple(column for column in _COLUMNS if column != "series_id")
# See scripts/time_series.py: MERGE keeps a batch of changed rows in one
# statement; the fallback is only reached by the SQLite engine used in tests.
_MERGE_DIALECTS = frozenset({"databricks", "postgresql"})

# Index references differ by published layer and must never be conflated.
_INDEX_REFERENCE = {
    "COICOP": "index reference 2015=100",
    "ALT": "index reference 2015=100",
    "CS": "index re-referenced to 100 each January",
}
_LEVEL_LABELS = {
    "all_items": "all items",
    "division": "COICOP division",
    "group": "COICOP group",
    "class": "COICOP class",
    "analytical_aggregate": "ONS analytical aggregate",
    "consumption_segment": "ONS consumption segment",
}


def legacy_identifier_sql(table: str) -> TextClause:
    """Count rows still using the superseded name-bearing identifier spelling.

    The old spelling appended the official name, so it always carries more than
    the three underscores of ``CPI_{family}_{node}_{native_id}``.
    """
    return text(
        f"SELECT COUNT(*) FROM {table} WHERE series_id LIKE 'CPI%' "
        "AND LENGTH(series_id) - LENGTH(REPLACE(series_id, '_', '')) > 3"
    )


_SELECT_SQL = text(f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE}")


def assert_current_series_ids(conn: Connection) -> None:
    """Refuse to mix pre-migration name-bearing identifiers with stable ones.

    Identifiers used to embed the official series name, so an ONS title edit
    forked the stored history. The current identifier carries only the family,
    hierarchy node and native ONS identifier. Mixing the two spellings in one
    database would silently split every affected series, so a database still
    holding the old spelling stops the run and is migrated deliberately.
    """
    for table in (_TABLE, _TIME_SERIES):
        legacy = conn.execute(legacy_identifier_sql(table)).scalar_one()
        if legacy:
            raise ValueError(
                f"{table} holds {legacy} rows using the superseded name-bearing series_id "
                "spelling; see COMPLIANCE.md for the reviewed migration before collecting"
            )


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
    assignments = ", ".join(f"{column} = source.{column}" for column in _UPDATE_COLUMNS)
    return text(
        f"MERGE INTO {_TABLE} AS target USING ({source}) AS source "
        "ON target.series_id = source.series_id "
        f"WHEN MATCHED THEN UPDATE SET {assignments}"
    )


_UPDATE_SQL = text(
    f"UPDATE {_TABLE} SET {', '.join(f'{column}=:{column}' for column in _UPDATE_COLUMNS)} "
    "WHERE series_id=:series_id"
)


def _write_batches(
    conn: Connection, rows: list[dict[str, Any]], operation: str, merge: bool
) -> None:
    """Apply rows in bounded statements, logging INFO progress per batch."""
    if not rows:
        return
    logger.info("Metadata %s: writing %d rows in batches of %d", operation, len(rows), BATCH_SIZE)
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        if merge:
            conn.execute(_merge_statement(len(batch)), _batch_parameters(batch))
        elif operation == "insert":
            conn.execute(_insert_statement(len(batch)), _batch_parameters(batch))
        else:
            conn.execute(_UPDATE_SQL, batch)
        logger.info(
            "Metadata %s progress: %d/%d rows",
            operation,
            min(start + BATCH_SIZE, len(rows)),
            len(rows),
        )


def _as_date(value: Any) -> date | None:
    """Normalize whatever a dialect returns for a DATE column into a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _series_descriptive_row(series_id: str, fields: dict[str, str]) -> dict[str, object]:
    """Build the descriptive columns from verified upstream ONS fields.

    ``parse_series_id`` supplies structural identity only. Every human-readable
    field comes from the workbook or CSV the observation itself came from, so a
    stored name is the name ONS published rather than a reconstruction.
    """
    _, family, _, native_id = parse_series_id(series_id)
    if fields.get("family") != family or fields.get("native_id") != native_id:
        raise ValueError(f"Upstream catalog does not describe {series_id}: {fields}")
    name = str(fields.get("name", "")).strip()
    if not name:
        raise ValueError(f"Upstream catalog carries no official name for {series_id}")
    frequency, unit, eco_group = "monthly", "index", "consumer_prices"
    if frequency not in FREQUENCIES or unit not in UNITS or eco_group not in ECO_GROUPS:
        raise ValueError(f"Invalid controlled vocabulary for {series_id}")
    level = _LEVEL_LABELS.get(str(fields.get("level", "")), str(fields.get("level", "")))
    classification = str(fields.get("classification", "")).strip()
    parent = str(fields.get("parent_series_id", "")).strip()
    description = (
        f"{fields['dataset']}. Official UK Consumer Prices Index level, "
        f"{_INDEX_REFERENCE[family]}; {level}"
        + (f" {classification}" if classification else "")
        + f"; native ONS identifier {native_id}"
        + (f"; aggregated into {parent}" if parent else "")
        + f". {fields['provenance']}."
    )
    return {
        "series_id": series_id,
        "name": f"UK CPI: {name}",
        "description": description,
        "country": COUNTRY_CURRENCY,
        "frequency": frequency,
        "unit": unit,
        "eco_group": eco_group,
        "source_url": fields["source_url"],
    }


def build_metadata_rows(
    parsed_by_date: dict[date, dict[str, float | None]],
    aggregates: dict[str, dict[str, Any]],
    collected_at: datetime,
    catalog: dict[str, dict[str, str]],
) -> list[dict[str, object]]:
    """Create one row for every extracted series present in the database."""
    series_ids = {series_id for values in parsed_by_date.values() for series_id in values}
    publish_date = get_last_publish_date()
    rows: list[dict[str, object]] = []
    for series_id in sorted(series_ids):
        aggregate = aggregates.get(series_id)
        if not aggregate:
            continue
        fields = catalog.get(series_id)
        if fields is None:
            raise ValueError(f"No upstream metadata was captured for {series_id}")
        row = _series_descriptive_row(series_id, fields)
        row.update(
            first_observation=_as_date(aggregate["first_observation"]),
            last_observation=_as_date(aggregate["last_observation"]),
            observation_count=int(aggregate["observation_count"]),
            last_publish_date=publish_date
            or _as_date(aggregate.get("last_collected_at"))
            or collected_at.date(),
            collected_at=collected_at,
        )
        rows.append(row)
    return rows


def upsert_metadata(
    conn: Connection,
    parsed_by_date: dict[date, dict[str, float | None]],
    collected_at: datetime,
    catalog: dict[str, dict[str, str]],
) -> tuple[int, int]:
    """Insert new metadata and update only genuinely changed rows."""
    desired = build_metadata_rows(
        parsed_by_date, get_series_aggregates(conn), collected_at, catalog
    )
    logger.info("Metadata upsert: evaluating %d series", len(desired))
    current_rows = (
        conn.execute(text(f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE}")).mappings().all()
    )
    current = {str(row["series_id"]): dict(row) for row in current_rows}
    inserts: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    for row in desired:
        existing = current.get(str(row["series_id"]))
        if existing is None:
            inserts.append(row)
        elif not all(
            _as_date(existing.get(column)) == row.get(column)
            if column.endswith("observation") or column == "last_publish_date"
            else existing.get(column) == row.get(column)
            for column in _COMPARABLE_COLUMNS
        ):
            updates.append(row)
    merge = conn.dialect.name in _MERGE_DIALECTS
    _write_batches(conn, inserts, "insert", merge=False)
    _write_batches(conn, updates, "update", merge=merge)
    logger.info("Metadata upsert: inserted=%d updated=%d", len(inserts), len(updates))
    return len(inserts), len(updates)
