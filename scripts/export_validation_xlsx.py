"""Export an analyst workbook that reproduces the datasets actually persisted.

Every sheet is read back from the database after the run has written it, so the
workbook is a view of stored rows rather than a snapshot of the process memory
that produced them.

The test the workbook has to pass is narrow and concrete: an analyst holding
only this file must be able to rebuild a published EX-CPI aggregate and see
how close they got. That needs four things together, which is why they are
four sheets --- the index levels, the operational shares that combine them,
the official basket those shares came from, and the crosswalk saying which
CDID is the complement of which aggregate. The Reconciliation sheet then shows
the arithmetic already carried out, so a reader can check their own working
against it rather than trusting a summary line in a log.
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
from scripts.reconciliation import RECONSTRUCTION_TOLERANCE, operational_share_id
from scripts.special_aggregate_vintages import mm23_original_weight_id
from scripts.special_aggregates import EX_CPI_SPECIAL_AGGREGATES, HEADLINE_INDEX_CDID

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


def _crosswalk(catalog: dict[str, dict[str, str]]) -> pd.DataFrame:
    """One row per aggregate, naming every identifier needed to rebuild it."""
    resolved = {
        str(entry.get("native_id", "")).upper(): series_id for series_id, entry in catalog.items()
    }
    rows: list[dict[str, Any]] = []
    for aggregate in EX_CPI_SPECIAL_AGGREGATES:
        rows.append(
            {
                "label": aggregate["label"],
                "index_cdid": aggregate["index_cdid"],
                "stored_index_series_id": resolved.get(aggregate["index_cdid"], ""),
                "complement_index_cdid": aggregate["complement_index_cdid"],
                "weight_cdid": aggregate["weight_cdid"],
                "stored_original_weight_id": mm23_original_weight_id(aggregate["weight_cdid"]),
                "stored_operational_share_id": operational_share_id(aggregate["weight_cdid"]),
                "complement_weight_cdid": aggregate["complement_weight_cdid"],
                "complement_stored_original_weight_id": mm23_original_weight_id(
                    aggregate["complement_weight_cdid"]
                ),
                "complement_stored_operational_share_id": operational_share_id(
                    aggregate["complement_weight_cdid"]
                ),
                "rate_12m_cdid": aggregate["rate_12m_cdid"],
                "complement_rate_12m_cdid": aggregate["complement_rate_12m_cdid"],
            }
        )
    return pd.DataFrame(rows)


def _method_sheet() -> pd.DataFrame:
    """State the reconstruction in words, beside the numbers that use it."""
    return pd.DataFrame(
        [
            {
                "step": 1,
                "detail": (
                    "Take the aggregate and its complement from Time Series, plus the "
                    f"published all-items CPI ({HEADLINE_INDEX_CDID})."
                ),
            },
            {
                "step": 2,
                "detail": (
                    "Read s_ex and s_c for that month from Operational Shares. They are "
                    "already normalised and sum to 1."
                ),
            },
            {
                "step": 3,
                "detail": (
                    "Rebase each index on the previous December: I(t)/I(Dec). Index levels "
                    "must not be combined directly -- see step 5."
                ),
            },
            {
                "step": 4,
                "detail": (
                    "Reconstruct: I_all(t) = I_all(Dec) * "
                    "( s_ex*I_ex(t)/I_ex(Dec) + s_c*I_c(t)/I_c(Dec) )."
                ),
            },
            {
                "step": 5,
                "detail": (
                    "Applying the Original Weights to index levels instead leaves residuals "
                    "up to 1.45 index points that grow with the size of the complement. That "
                    "is model error, not rounding, which is why Operational Shares exist."
                ),
            },
            {
                "step": 6,
                "detail": (
                    f"Expect |residual| <= {RECONSTRUCTION_TOLERANCE} index points. Observed "
                    "max over 1996-2026 is 0.18, the rounding floor of a file published to "
                    "one decimal place."
                ),
            },
            {
                "step": 7,
                "detail": (
                    "Original Weights are the published MM23 basket in parts per thousand, "
                    "with weight_base_year. Operational Shares are derived from them, in "
                    "[0, 1]. The two are kept apart on purpose."
                ),
            },
        ]
    )


def export_validation_xlsx(
    engine: Engine,
    catalog: dict[str, dict[str, str]],
    reconciliation_rows: list[dict[str, Any]],
    output_path: Path,
    *,
    as_of: date,
) -> Path:
    """Write the audit workbook from persisted rows and return its path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    observations = _latest_frame(engine, TIME_SERIES_TABLE, "value", as_of)
    shares = _latest_frame(engine, WEIGHTS_TABLE, "weight", as_of)
    original = _latest_frame(engine, ORIGINAL_WEIGHTS_TABLE, "weight", as_of)
    metadata = _table_frame(
        engine,
        f"SELECT series_id, name, description, country, frequency, unit, first_observation, "
        f"last_observation, observation_count, eco_group, source_url, last_publish_date "
        f"FROM {SCHEMA_NAME}.{METADATA_TABLE} ORDER BY series_id",
    )
    original_regimes = _table_frame(
        engine,
        f"SELECT series_id, reference_date, weight, weight_base_year, vintage_date "
        f"FROM {SCHEMA_NAME}.{ORIGINAL_WEIGHTS_TABLE} ORDER BY series_id, reference_date",
    )
    run = pd.DataFrame(
        [
            {"field": "as_of", "value": as_of.isoformat()},
            {"field": "schema", "value": SCHEMA_NAME},
            {
                "field": "index_series",
                "value": int(observations["series_id"].nunique()) if not observations.empty else 0,
            },
            {
                "field": "operational_share_series",
                "value": int(shares["series_id"].nunique()) if not shares.empty else 0,
            },
            {
                "field": "original_weight_series",
                "value": int(original["series_id"].nunique()) if not original.empty else 0,
            },
            {"field": "reconciliation_checks", "value": len(reconciliation_rows)},
            {
                "field": "reconciliation_failures",
                "value": sum(1 for row in reconciliation_rows if not row.get("passed", False)),
            },
            {"field": "reconstruction_tolerance", "value": RECONSTRUCTION_TOLERANCE},
        ]
    )

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        run.to_excel(writer, sheet_name="Run", index=False)
        _method_sheet().to_excel(writer, sheet_name="Method", index=False)
        _pivot(observations).to_excel(
            writer, sheet_name="Time Series", index_label="reference_date"
        )
        _pivot(shares).to_excel(
            writer, sheet_name="Operational Shares", index_label="reference_date"
        )
        _pivot(original).to_excel(
            writer, sheet_name="Original Weights", index_label="reference_date"
        )
        original_regimes.to_excel(writer, sheet_name="Weight Regimes", index=False)
        _crosswalk(catalog).to_excel(writer, sheet_name="Series Map", index=False)
        pd.DataFrame(reconciliation_rows).to_excel(writer, sheet_name="Reconciliation", index=False)
        metadata.to_excel(writer, sheet_name="Metadata", index=False)
    logger.info("Validation workbook written to %s", output_path)
    return output_path
