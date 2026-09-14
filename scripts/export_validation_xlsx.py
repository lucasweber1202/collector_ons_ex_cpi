"""Export an analyst workbook that reproduces the datasets actually persisted.

Every sheet is read back from the database after the run has written it, so the
workbook is a view of stored rows rather than a snapshot of the process memory
that produced them. An analyst can rebuild any published aggregate from the
Time Series, Weights and Series Map sheets alone.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from scripts.config import (
    METADATA_TABLE,
    ORIGINAL_WEIGHTS_TABLE,
    SCHEMA_NAME,
    TIME_SERIES_TABLE,
    WEIGHTS_TABLE,
)

logger = logging.getLogger(__name__)

_LATEST_VINTAGE_SQL = """
SELECT series_id, reference_date, {value_column} AS value, vintage_date
FROM (
    SELECT series_id, reference_date, vintage_date, {value_column}, collected_at,
           ROW_NUMBER() OVER (
               PARTITION BY series_id, reference_date
               ORDER BY vintage_date DESC, collected_at DESC
           ) AS rn
    FROM {schema}.{table}
    WHERE vintage_date <= :as_of
) ranked
WHERE rn = 1
"""


def _latest_frame(engine: Engine, table: str, value_column: str, as_of: date) -> pd.DataFrame:
    """Return the latest stored vintage of one table as of ``as_of``."""
    sql = text(
        _LATEST_VINTAGE_SQL.format(value_column=value_column, schema=SCHEMA_NAME, table=table)
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"as_of": as_of}).mappings().all()
    return pd.DataFrame([dict(row) for row in rows])


def _pivot(frame: pd.DataFrame) -> pd.DataFrame:
    """Pivot stored long rows into dates by series, the analyst-facing shape."""
    if frame.empty:
        return pd.DataFrame()
    return frame.pivot(index="reference_date", columns="series_id", values="value").sort_index()


def _table_frame(engine: Engine, sql: str) -> pd.DataFrame:
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).mappings().all()
    return pd.DataFrame([dict(row) for row in rows])


def export_validation_xlsx(
    engine: Engine,
    catalog: dict[str, dict[str, str]],
    original_weight_catalog: dict[str, dict[str, str]],
    validation_rows: list[dict[str, Any]],
    output_path: Path,
    *,
    as_of: date,
) -> Path:
    """Write the audit workbook from persisted rows and return its path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    observations = _latest_frame(engine, TIME_SERIES_TABLE, "value", as_of)
    weights = _latest_frame(engine, WEIGHTS_TABLE, "weight", as_of)
    original = _latest_frame(engine, ORIGINAL_WEIGHTS_TABLE, "weight", as_of)
    metadata = _table_frame(
        engine,
        f"SELECT series_id, name, description, country, frequency, unit, first_observation, "
        f"last_observation, observation_count, eco_group, source_url, last_publish_date "
        f"FROM {SCHEMA_NAME}.{METADATA_TABLE} ORDER BY series_id",
    )
    series_map = metadata.copy()
    if not series_map.empty:
        for column in (
            "family",
            "node",
            "level",
            "native_id",
            "classification",
            "parent_series_id",
            "dataset",
            "provenance",
        ):
            series_map[column] = series_map["series_id"].map(
                lambda series_id, key=column: catalog.get(series_id, {}).get(key, "")
            )
    weight_map = pd.DataFrame(
        [
            {"original_weight_series_id": key, **fields}
            for key, fields in sorted(original_weight_catalog.items())
        ]
    )
    run = pd.DataFrame(
        [
            {"property": "as_of_vintage_date", "value": as_of.isoformat()},
            {"property": "view", "value": "latest vintage per (series_id, reference_date)"},
            {"property": "database_schema", "value": SCHEMA_NAME},
            {"property": "stored_observations", "value": len(observations)},
            {"property": "stored_operational_weights", "value": len(weights)},
            {"property": "stored_original_weights", "value": len(original)},
            {"property": "stored_metadata_rows", "value": len(metadata)},
            {"property": "validation_checks", "value": len(validation_rows)},
        ]
    )
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        run.to_excel(writer, sheet_name="Run", index=False)
        _pivot(observations).to_excel(
            writer, sheet_name="Time Series", index_label="reference_date"
        )
        _pivot(weights).to_excel(writer, sheet_name="Weights", index_label="reference_date")
        _pivot(original).to_excel(
            writer, sheet_name="Original Weights", index_label="reference_date"
        )
        series_map.to_excel(writer, sheet_name="Series Map", index=False)
        weight_map.to_excel(writer, sheet_name="Original Weight Map", index=False)
        pd.DataFrame(validation_rows).to_excel(writer, sheet_name="Validation", index=False)
    logger.info(
        "Validation workbook: %d observations, %d operational weights, %d original weights",
        len(observations),
        len(weights),
        len(original),
    )
    return output_path
