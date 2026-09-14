"""Same-day revisions update today's vintage; later revisions preserve history."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from scripts.original_weights import upsert_original_weights
from scripts.time_series import upsert_time_series
from scripts.weights import upsert_weights

MONTH = date(2026, 1, 1)
FIRST = datetime(2026, 8, 19)  # noqa: DTZ001
LATER = datetime(2026, 8, 20)  # noqa: DTZ001


@pytest.mark.parametrize(
    ("table", "column", "upsert"),
    [
        ("time_series", "value", upsert_time_series),
        ("weights", "weight", upsert_weights),
    ],
)
def test_revision_history(
    engine: Engine,
    table: str,
    column: str,
    upsert: Callable[..., tuple[int, int]],
) -> None:
    for values, collected_at, expected in (
        ({"S": 0.1}, FIRST, (1, 0)),
        ({"S": 0.2}, FIRST, (0, 0)),
        ({"S": 0.3}, LATER, (0, 1)),
        ({"S": 0.4}, LATER, (0, 0)),
        ({"S": 0.400000000001}, LATER, (0, 0)),
    ):
        with engine.begin() as conn:
            assert upsert(conn, {MONTH: values}, collected_at) == expected
    with engine.connect() as conn:
        assert conn.execute(
            text(f"SELECT {column} FROM collector_ons_ex_cpi.{table} ORDER BY vintage_date")
        ).scalars().all() == [0.2, 0.4]


def _rows(weight: float, base_year: int = 2026) -> list[dict[str, object]]:
    return [
        {
            "series_id": "CPI_W1_0",
            "reference_date": MONTH,
            "weight": weight,
            "weight_base_year": base_year,
        }
    ]


def test_original_weight_revision_history(engine: Engine) -> None:
    for weight, collected_at, expected in (
        (1000.0, FIRST, (1, 0)),
        (999.0, FIRST, (0, 0)),
        (998.0, LATER, (0, 1)),
        (998.0, LATER, (0, 0)),
    ):
        with engine.begin() as conn:
            assert upsert_original_weights(conn, _rows(weight), collected_at) == expected
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT weight FROM collector_ons_ex_cpi.original_weights ORDER BY vintage_date")
        ).scalars().all() == [999.0, 998.0]


def test_a_corrected_regime_year_is_a_real_change(engine: Engine) -> None:
    """The published basket year is part of the official record, not a derivation."""
    with engine.begin() as conn:
        assert upsert_original_weights(conn, _rows(7.691, 2026), FIRST) == (1, 0)
    with engine.begin() as conn:
        assert upsert_original_weights(conn, _rows(7.691, 2025), LATER) == (0, 1)
    with engine.connect() as conn:
        assert conn.execute(
            text(
                "SELECT weight_base_year FROM collector_ons_ex_cpi.original_weights "
                "ORDER BY vintage_date"
            )
        ).scalars().all() == [2026, 2025]
