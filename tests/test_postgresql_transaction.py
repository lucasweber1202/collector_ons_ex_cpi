"""PostgreSQL transaction rollback gate for EX-CPI product tables."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, text

from scripts.init_db import init_db
from scripts.original_weights import upsert_original_weights
from scripts.time_series import upsert_time_series


@pytest.mark.skipif(not os.getenv("POSTGRES_TEST_URL"), reason="PostgreSQL test URL unavailable")
def test_postgresql_rolls_back_observations_and_weights() -> None:
    engine = create_engine(os.environ['POSTGRES_TEST_URL'])
    with engine.begin() as conn:
        conn.execute(text('DROP SCHEMA IF EXISTS collector_ons_ex_cpi CASCADE'))
    init_db(engine)
    collected_at = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    observations = {
        date(2026, 8, 1): {"EXCPI_INDEX_NATIVE_DKC6": 102.5}
    }
    weights = [
        {
            "series_id": "EXCPI_WEIGHT_NATIVE_A9FU",
            "reference_date": date(2026, 8, 1),
            "weight": 700.0,
            "weight_base_year": 2026,
        }
    ]
    with (
        pytest.raises(RuntimeError, match="forced failure after original_weights"),
        engine.begin() as conn,
    ):
        upsert_time_series(conn, observations, collected_at)
        upsert_original_weights(conn, weights, collected_at)
        raise RuntimeError("forced failure after original_weights")
    with engine.connect() as conn:
        for table in ('time_series', 'original_weights', 'metadata'):
            assert conn.execute(
                text(f"SELECT COUNT(*) FROM collector_ons_ex_cpi.{table}")
            ).scalar_one() == 0
    engine.dispose()
