"""The audit workbook must be a view of persisted rows, not of process memory."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy.engine import Engine

from scripts import segments
from scripts.export_validation_xlsx import export_validation_xlsx
from scripts.metadata import upsert_metadata
from scripts.original_weights import upsert_original_weights
from scripts.time_series import upsert_time_series
from scripts.weights import upsert_weights
from tests.conftest import catalog_entry

FIRST = datetime(2026, 8, 19)  # noqa: DTZ001
LATER = datetime(2026, 8, 20)  # noqa: DTZ001
MONTH = date(2026, 7, 1)
_SERIES = catalog_entry(
    "COICOP", "D01", "D7BU", "FOOD AND NON-ALCOHOLIC BEVERAGES", classification="1"
)
SERIES_ID, CATALOG = _SERIES[0], dict([_SERIES])
WEIGHT_CATALOG = {
    "CPI_W1_01": {
        "code": "01",
        "name": "Food and non-alcoholic beverages",
        "native_id": "CHZR",
        "mapped_series_id": SERIES_ID,
        "dataset": "W1",
        "source_url": "https://www.ons.gov.uk/",
    }
}


def _populate(engine: Engine) -> None:
    observations: dict[date, dict[str, float | None]] = {MONTH: {SERIES_ID: 144.029}}
    with engine.begin() as conn:
        upsert_time_series(conn, observations, FIRST)
        upsert_weights(conn, {MONTH: {SERIES_ID: 0.25}}, FIRST)
        upsert_original_weights(
            conn,
            [
                {
                    "series_id": "CPI_W1_01",
                    "reference_date": MONTH,
                    "weight": 108.8,
                    "weight_base_year": 2026,
                }
            ],
            FIRST,
        )
        upsert_metadata(conn, observations, FIRST, CATALOG)


def test_workbook_reports_the_stored_rows_and_their_vintage(engine: Engine, tmp_path: Path) -> None:
    _populate(engine)
    output = export_validation_xlsx(
        engine,
        CATALOG,
        WEIGHT_CATALOG,
        [{"check": "weight_sum", "residual": 0.0, "passed": True}],
        tmp_path / "review.xlsx",
        as_of=LATER.date(),
    )
    with pd.ExcelFile(output) as workbook:
        assert {
            "Run",
            "Time Series",
            "Weights",
            "Original Weights",
            "Series Map",
            "Original Weight Map",
            "Validation",
        } == set(workbook.sheet_names)
        run = pd.read_excel(workbook, "Run").set_index("property")["value"]
        assert run["as_of_vintage_date"] == LATER.date().isoformat()
        assert int(run["stored_observations"]) == 1
        assert pd.read_excel(workbook, "Time Series").iloc[0][SERIES_ID] == 144.029
        assert pd.read_excel(workbook, "Weights").iloc[0][SERIES_ID] == 0.25
        assert pd.read_excel(workbook, "Original Weights").iloc[0]["CPI_W1_01"] == 108.8
        series_map = pd.read_excel(workbook, "Series Map").iloc[0]
        assert series_map["native_id"] == "D7BU"
        assert pd.isna(series_map["parent_series_id"])
        assert series_map["provenance"]
        weight_map = pd.read_excel(workbook, "Original Weight Map").iloc[0]
        assert weight_map["code"] == 1
        assert weight_map["native_id"] == "CHZR"
        assert weight_map["mapped_series_id"] == SERIES_ID


def test_workbook_exports_the_requested_vintage_only(engine: Engine, tmp_path: Path) -> None:
    """An earlier as-of must show the value as it stood then, not the revision."""
    _populate(engine)
    with engine.begin() as conn:
        upsert_time_series(conn, {MONTH: {SERIES_ID: 144.5}}, LATER)
    output = export_validation_xlsx(
        engine, CATALOG, WEIGHT_CATALOG, [], tmp_path / "as_of.xlsx", as_of=FIRST.date()
    )
    assert pd.read_excel(output, "Time Series").iloc[0][SERIES_ID] == 144.029
    output = export_validation_xlsx(
        engine, CATALOG, WEIGHT_CATALOG, [], tmp_path / "latest.xlsx", as_of=LATER.date()
    )
    assert pd.read_excel(output, "Time Series").iloc[0][SERIES_ID] == 144.5


def test_segment_provenance_reaches_the_series_map(engine: Engine, tmp_path: Path) -> None:
    observations: dict[date, dict[str, float | None]] = {MONTH: {"CPI_CS_SEG_220107": 103.867}}
    catalog = {
        "CPI_CS_SEG_220107": {
            "family": "CS",
            "node": "SEG",
            "level": "consumption_segment",
            "native_id": "220107",
            "name": "PUB -HOT MEAL",
            "classification": "11.1.1.1",
            "dataset": segments.SEGMENT_DATASET,
            "source_url": segments.SEGMENTS_PAGE_URL,
            "provenance": segments.SEGMENT_PROVENANCE,
            "parent_series_id": "CPI_COICOP_C1111_D7EW",
        }
    }
    with engine.begin() as conn:
        upsert_time_series(conn, observations, FIRST)
        upsert_metadata(conn, observations, FIRST, catalog)
    output = export_validation_xlsx(
        engine, catalog, {}, [], tmp_path / "segments.xlsx", as_of=FIRST.date()
    )
    row = pd.read_excel(output, "Series Map").iloc[0]
    assert row["level"] == "consumption_segment"
    assert row["parent_series_id"] == "CPI_COICOP_C1111_D7EW"
    assert "research data" in row["provenance"]


def test_an_empty_database_still_produces_every_sheet(engine: Engine, tmp_path: Path) -> None:
    """A workbook must never silently omit a dataset just because it is empty."""
    output = export_validation_xlsx(engine, {}, {}, [], tmp_path / "empty.xlsx", as_of=FIRST.date())
    with pd.ExcelFile(output) as workbook:
        assert {
            "Run",
            "Time Series",
            "Weights",
            "Original Weights",
            "Series Map",
            "Original Weight Map",
            "Validation",
        } == set(workbook.sheet_names)
        run = pd.read_excel(workbook, "Run").set_index("property")["value"]
        assert int(run["stored_observations"]) == 0
        assert int(run["stored_metadata_rows"]) == 0
