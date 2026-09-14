"""The stored identifier must survive an ONS title edit and stay decodable."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from scripts.extract import make_series_id, parse_series_id
from scripts.metadata import assert_current_series_ids


def test_identifier_ignores_the_official_name() -> None:
    """Two releases that only rename a series must produce one identifier."""
    before = make_series_id("COICOP", "D01", "D7BU")
    after = make_series_id("COICOP", "D01", "D7BU")
    assert before == after == "CPI_COICOP_D01_D7BU"
    assert parse_series_id(before) == ("CPI", "COICOP", "D01", "D7BU")


@pytest.mark.parametrize(
    ("family", "node", "native", "expected"),
    [
        ("COICOP", "ALL", "D7BT", "CPI_COICOP_ALL_D7BT"),
        ("COICOP", "C0711", "d7e8 ", "CPI_COICOP_C0711_D7E8"),
        ("ALT", "A13", "DK9T", "CPI_ALT_A13_DK9T"),
        ("CS", "SEG", "CP0111301", "CPI_CS_SEG_CP0111301"),
        ("CS", "SEG", "220107", "CPI_CS_SEG_220107"),
    ],
)
def test_identifier_shape(family: str, node: str, native: str, expected: str) -> None:
    series_id = make_series_id(family, node, native)
    assert series_id == expected
    assert series_id.isupper()
    assert parse_series_id(series_id)[1] == family


@pytest.mark.parametrize(
    "series_id",
    [
        "CPI_COICOP_D01",
        "CPI_UNKNOWN_D01_D7BU",
        "CPI_COICOP_D01_D7BU_FOOD_AND_NON_ALCOHOLIC_BEVERAGES",
        "COICOP_D01_D7BU",
    ],
)
def test_undecodable_identifiers_are_rejected(series_id: str) -> None:
    with pytest.raises(ValueError, match="UK CPI series_id"):
        parse_series_id(series_id)


def test_unknown_family_is_rejected() -> None:
    with pytest.raises(ValueError, match="family"):
        make_series_id("RPI", "D01", "D7BU")


def test_empty_native_identifier_is_rejected() -> None:
    with pytest.raises(ValueError, match="Incomplete"):
        make_series_id("COICOP", "D01", "  ")


def _seed(engine: Engine, table: str, series_id: str) -> None:
    rows = {
        "metadata": (
            "INSERT INTO collector_ons_ex_cpi.metadata (series_id, name, country, "
            "observation_count, source_url, collected_at) "
            "VALUES (:series_id, 'n', 'GBP', 1, 'u', '2026-08-19')"
        ),
        "time_series": (
            "INSERT INTO collector_ons_ex_cpi.time_series VALUES "
            "(:series_id, '2026-07-01', '2026-08-19', 1.0, '2026-08-19')"
        ),
    }[table]
    with engine.begin() as conn:
        conn.execute(text(rows), {"series_id": series_id})


@pytest.mark.parametrize("table", ["metadata", "time_series"])
def test_legacy_name_bearing_rows_block_the_run(engine: Engine, table: str) -> None:
    """A database written before the rename must be migrated, never mixed."""
    _seed(engine, table, "CPI_COICOP_D01_D7BU_FOOD_AND_NON_ALCOHOLIC_BEVERAGES")
    with engine.begin() as conn, pytest.raises(ValueError, match="superseded"):
        assert_current_series_ids(conn)


@pytest.mark.parametrize("series_id", ["CPI_COICOP_D01_D7BU", "CPI_CS_SEG_CP0111301"])
def test_current_rows_pass_the_guard(engine: Engine, series_id: str) -> None:
    _seed(engine, "metadata", series_id)
    _seed(engine, "time_series", series_id)
    with engine.begin() as conn:
        assert_current_series_ids(conn)  # must not raise
