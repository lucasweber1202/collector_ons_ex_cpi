"""Database-level regression check for no-op second writes."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from scripts.metadata import upsert_metadata
from scripts.run_logs import insert_run_log
from scripts.time_series import get_max_reference_date, upsert_time_series
from scripts.weights import upsert_weights
from tests.conftest import catalog_entry

_SERIES = catalog_entry("COICOP", "ALL", "D7BT", "CPI ALL ITEMS", level="all_items")
SERIES_ID, CATALOG = _SERIES[0], dict([_SERIES])
COLLECTED_AT = datetime(2026, 8, 19, 6, 0)  # noqa: DTZ001


def test_second_write_is_data_noop_and_log_is_appended(engine: Engine) -> None:
    observations: dict[date, dict[str, float | None]] = {
        date(2026, 6, 1): {SERIES_ID: 142.5},
        date(2026, 7, 1): {SERIES_ID: 142.933},
    }
    weights = {date(2026, 7, 1): {SERIES_ID: 1.0}}

    for expected_new, expected_inserted in ((2, 1), (0, 0)):
        with engine.begin() as conn:
            assert upsert_time_series(conn, observations, COLLECTED_AT)[0] == expected_new
            assert upsert_weights(conn, weights, COLLECTED_AT)[0] == (1 if expected_new else 0)
            inserted, updated = upsert_metadata(conn, observations, COLLECTED_AT, CATALOG)
            assert (inserted, updated) == (expected_inserted, 0)
        insert_run_log(engine, COLLECTED_AT, COLLECTED_AT, "success", "ok", None)

    assert get_max_reference_date(engine) == date(2026, 7, 1)
    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT COUNT(*) FROM collector_ons_ex_cpi.time_series")).scalar_one()
            == 2
        )
        assert conn.execute(text("SELECT COUNT(*) FROM collector_ons_ex_cpi.logs")).scalar_one() == 2
        row = conn.execute(
            text(
                "SELECT observation_count, first_observation, last_observation "
                "FROM collector_ons_ex_cpi.metadata"
            )
        ).one()
    assert row[0] == 2
    assert (str(row[1])[:10], str(row[2])[:10]) == ("2026-06-01", "2026-07-01")
