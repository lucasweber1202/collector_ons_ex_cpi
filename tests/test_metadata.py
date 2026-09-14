"""Metadata must carry verified upstream ONS fields, not reconstructed text."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from scripts import extract, metadata, segments
from scripts.time_series import upsert_time_series
from tests.conftest import catalog_entry

COLLECTED_AT = datetime(2026, 8, 19, 6, 0)  # noqa: DTZ001
_DIVISION = catalog_entry(
    "COICOP",
    "D01",
    "D7BU",
    "FOOD AND NON-ALCOHOLIC BEVERAGES",
    classification="1",
    level="division",
    parent="CPI_COICOP_ALL_D7BT",
)
DIVISION, DIVISION_FIELDS = _DIVISION
SEGMENT = "CPI_CS_SEG_CP0111301"
SEGMENT_FIELDS = {
    "family": "CS",
    "node": "SEG",
    "level": "consumption_segment",
    "native_id": "CP0111301",
    "name": "BREAD, WHITE",
    "classification": "01.1.1.3",
    "dataset": segments.SEGMENT_DATASET,
    "source_url": segments.SEGMENTS_PAGE_URL,
    "provenance": segments.SEGMENT_PROVENANCE,
    "parent_series_id": "CPI_COICOP_C0111_D7D5",
}
CATALOG = {DIVISION: DIVISION_FIELDS, SEGMENT: SEGMENT_FIELDS}


def test_official_name_is_stored_verbatim() -> None:
    row = metadata._series_descriptive_row(DIVISION, DIVISION_FIELDS)
    assert row["name"] == "UK CPI: FOOD AND NON-ALCOHOLIC BEVERAGES"
    assert "D7BU" in str(row["description"])
    assert "COICOP division 1" in str(row["description"])
    assert "aggregated into CPI_COICOP_ALL_D7BT" in str(row["description"])
    assert row["source_url"] == extract.SOURCE_URL
    assert (row["country"], row["frequency"], row["unit"], row["eco_group"]) == (
        "GBP",
        "monthly",
        "index",
        "consumer_prices",
    )


def test_each_layer_keeps_its_own_index_reference_and_provenance() -> None:
    """A 2015=100 level and a January-referenced level must stay distinguishable."""
    coicop = str(metadata._series_descriptive_row(DIVISION, DIVISION_FIELDS)["description"])
    segment = str(metadata._series_descriptive_row(SEGMENT, SEGMENT_FIELDS)["description"])
    assert "index reference 2015=100" in coicop
    assert "not accredited official statistics" in coicop
    assert "re-referenced to 100 each January" in segment
    assert "research data" in segment
    assert segments.SEGMENTS_PAGE_URL != extract.SOURCE_URL
    assert (
        metadata._series_descriptive_row(SEGMENT, SEGMENT_FIELDS)["source_url"]
        == segments.SEGMENTS_PAGE_URL
    )


def test_catalog_describing_another_series_is_rejected() -> None:
    """The name must come from the row the observation came from."""
    with pytest.raises(ValueError, match="does not describe"):
        metadata._series_descriptive_row(SEGMENT, DIVISION_FIELDS)


def test_missing_official_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="no official name"):
        metadata._series_descriptive_row(DIVISION, {**DIVISION_FIELDS, "name": "  "})


def test_a_series_without_upstream_metadata_stops_the_run() -> None:
    with pytest.raises(ValueError, match="No upstream metadata"):
        metadata.build_metadata_rows(
            {date(2026, 7, 1): {"CPI_COICOP_D02_D7BV": 100.0}},
            {"CPI_COICOP_D02_D7BV": {"first_observation": None}},
            COLLECTED_AT,
            CATALOG,
        )


def test_history_columns_come_from_the_database_not_the_extraction_window(
    engine: Engine,
) -> None:
    """A five-month rewind must not shrink a thirty-eight-year history."""
    history: dict[date, dict[str, float | None]] = {
        date(year, 7, 1): {DIVISION: 100.0 + year} for year in (2024, 2025, 2026)
    }
    with engine.begin() as conn:
        upsert_time_series(conn, history, COLLECTED_AT)
        metadata.upsert_metadata(conn, history, COLLECTED_AT, CATALOG)
    rewind: dict[date, dict[str, float | None]] = {date(2026, 7, 1): {DIVISION: 2126.0}}
    with engine.begin() as conn:
        assert metadata.upsert_metadata(conn, rewind, COLLECTED_AT, CATALOG) == (0, 0)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT observation_count, first_observation, last_observation, last_publish_date "
                "FROM collector_ons_ex_cpi.metadata"
            )
        ).one()
    assert row[0] == 3
    assert str(row[1])[:10] == "2024-07-01"
    assert str(row[2])[:10] == "2026-07-01"


def test_a_renamed_series_updates_in_place_without_a_new_row(engine: Engine) -> None:
    """A title edit changes metadata only; the identifier and history survive."""
    observations: dict[date, dict[str, float | None]] = {date(2026, 7, 1): {DIVISION: 144.029}}
    with engine.begin() as conn:
        upsert_time_series(conn, observations, COLLECTED_AT)
        assert metadata.upsert_metadata(conn, observations, COLLECTED_AT, CATALOG) == (1, 0)
    renamed = {DIVISION: {**DIVISION_FIELDS, "name": "FOOD AND NON ALCOHOLIC BEVERAGES"}}
    with engine.begin() as conn:
        assert metadata.upsert_metadata(conn, observations, COLLECTED_AT, renamed) == (0, 1)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT series_id, name FROM collector_ons_ex_cpi.metadata")).all()
    assert rows == [(DIVISION, "UK CPI: FOOD AND NON ALCOHOLIC BEVERAGES")]
