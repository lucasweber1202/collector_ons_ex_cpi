"""Auditable MM23 component identities and safe migration of legacy weight keys."""

from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection

from scripts.config import SCHEMA_NAME
from scripts.extract import SOURCE_URL
from scripts.special_aggregates import EX_CPI_SPECIAL_AGGREGATES, HEADLINE_INDEX_CDID

DATASET = "ONS Consumer price inflation time series (MM23)"
TABLE = f"{SCHEMA_NAME}.weight_component_crosswalk"


def component_rows() -> list[dict[str, str | None]]:
    """One reviewed native MM23 weight per actual index component."""
    rows: list[dict[str, str | None]] = []
    for entry in EX_CPI_SPECIAL_AGGREGATES:
        target = f"EXCPI_INDEX_NATIVE_{entry['index_cdid']}"
        for role, index_cdid, weight_cdid in (
            ("exclusion", entry["index_cdid"], entry["weight_cdid"]),
            ("complement", entry["complement_index_cdid"], entry["complement_weight_cdid"]),
        ):
            rows.append(
                {
                    "aggregate_series_id": target,
                    "series_id": f"EXCPI_INDEX_NATIVE_{index_cdid}",
                    "native_index_cdid": index_cdid,
                    "native_weight_cdid": weight_cdid,
                    "role": role,
                    "source_dataset": DATASET,
                    "source_url": SOURCE_URL,
                }
            )
        rows.append(
            {
                "aggregate_series_id": target,
                "series_id": f"EXCPI_INDEX_NATIVE_{HEADLINE_INDEX_CDID}",
                "native_index_cdid": HEADLINE_INDEX_CDID,
                "native_weight_cdid": None,
                "role": "headline",
                "source_dataset": DATASET,
                "source_url": SOURCE_URL,
            }
        )
    return rows


def init_crosswalk(conn: Connection) -> None:
    conn.execute(
        text(
            f"""CREATE TABLE IF NOT EXISTS {TABLE} (
            aggregate_series_id VARCHAR(200) NOT NULL,
            series_id VARCHAR(200) NOT NULL,
            native_index_cdid VARCHAR(20) NOT NULL,
            native_weight_cdid VARCHAR(20),
            role VARCHAR(20) NOT NULL,
            source_dataset VARCHAR(500) NOT NULL,
            source_url VARCHAR(1000) NOT NULL,
            CONSTRAINT pk_weight_component_crosswalk PRIMARY KEY (aggregate_series_id, role)
            )"""
        )
    )


def upsert_crosswalk(conn: Connection) -> None:
    """Verify persisted provenance; never overwrite an unexpected old mapping."""
    existing = {
        (r["aggregate_series_id"], r["role"]): dict(r)
        for r in conn.execute(text(f"SELECT * FROM {TABLE}")).mappings()
    }
    for row in component_rows():
        key = (row["aggregate_series_id"], row["role"])
        if key in existing:
            if existing[key] != row:
                raise ValueError(f"MM23 crosswalk drift for {key}: {existing[key]} != {row}")
            continue
        conn.execute(
            text(
                f"INSERT INTO {TABLE} (aggregate_series_id, series_id, "
                "native_index_cdid, native_weight_cdid, role, source_dataset, source_url) "
                "VALUES (:aggregate_series_id, :series_id, :native_index_cdid, "
                ":native_weight_cdid, :role, :source_dataset, :source_url)"
            ),
            row,
        )


def migrate_legacy_weight_ids(conn: Connection) -> None:
    """Re-key old native weight/share rows transactionally, retaining every other column.

    The preflight checks ALL keys before changing ANY row. An already-migrated
    database is a no-op; a mixed database with colliding natural keys fails.
    """
    for table, prefix in (
        ("weights", "EXCPI_SHARE_NATIVE_"),
        ("original_weights", "EXCPI_WEIGHT_NATIVE_"),
    ):
        full_table = f"{SCHEMA_NAME}.{table}"
        for row in component_rows():
            native = row["native_weight_cdid"]
            if native is None:
                continue
            old = prefix + native
            new = row["series_id"]
            old_count = conn.execute(
                text(f"SELECT COUNT(*) FROM {full_table} WHERE series_id=:id"), {"id": old}
            ).scalar_one()
            if not old_count:
                continue
            collisions = conn.execute(
                text(
                    f"SELECT COUNT(*) FROM {full_table} legacy "
                    f"JOIN {full_table} current ON current.series_id=:new "
                    "AND current.reference_date=legacy.reference_date "
                    "AND current.vintage_date=legacy.vintage_date "
                    "WHERE legacy.series_id=:old"
                ),
                {"old": old, "new": new},
            ).scalar_one()
            if collisions:
                raise ValueError(
                    f"Ambiguous {table} migration {old} -> {new}: {collisions} collisions"
                )
    for table, prefix in (
        ("weights", "EXCPI_SHARE_NATIVE_"),
        ("original_weights", "EXCPI_WEIGHT_NATIVE_"),
    ):
        full_table = f"{SCHEMA_NAME}.{table}"
        for row in component_rows():
            native = row["native_weight_cdid"]
            if native is None:
                continue
            conn.execute(
                text(f"UPDATE {full_table} SET series_id=:new WHERE series_id=:old"),
                {"old": prefix + native, "new": row["series_id"]},
            )


def supporting_observations(
    monthly_indices: dict[date, dict[str, float]], start: date
) -> dict[date, dict[str, float]]:
    """Retain headline and complements, including the preceding December base."""
    ids = {row["native_index_cdid"] for row in component_rows() if row["role"] != "exclusion"}
    earliest = date(start.year - 1, 12, 1)
    return {
        month: {
            f"EXCPI_INDEX_NATIVE_{cdid}": value for cdid, value in values.items() if cdid in ids
        }
        for month, values in monthly_indices.items()
        if month >= earliest and any(cdid in values for cdid in ids)
    }


def supporting_catalog() -> dict[str, dict[str, str]]:
    """Explicitly label supporting reconstruction inputs, never forecast targets."""
    result: dict[str, dict[str, str]] = {}
    for row in component_rows():
        if row["role"] == "exclusion":
            continue
        sid = str(row["series_id"])
        result.setdefault(
            sid,
            {
                "family": "INDEX",
                "node": "NATIVE",
                "level": "supporting_reconstruction",
                "native_id": str(row["native_index_cdid"]),
                "name": f"ONS MM23 {row['role']} index {row['native_index_cdid']}",
                "classification": "supporting reconstruction series; not a forecast target",
                "dataset": DATASET,
                "source_url": SOURCE_URL,
                "provenance": "ONS MM23 published index level",
                "parent_series_id": str(row["aggregate_series_id"]),
            },
        )
    return result
