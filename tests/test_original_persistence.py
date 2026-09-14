"""Official basket regimes and revision vintages remain separate."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from scripts.original_weights import upsert_original_weights

FIRST = datetime(2026, 8, 19)  # noqa: DTZ001
LATER = datetime(2026, 8, 20)  # noqa: DTZ001


def _rows(january: float, february: float) -> list[dict[str, object]]:
    return [
        {
            "series_id": "CPI_W1_0",
            "reference_date": date(2026, 1, 1),
            "weight": january,
            "weight_base_year": 2026,
        },
        {
            "series_id": "CPI_W1_0",
            "reference_date": date(2026, 2, 1),
            "weight": february,
            "weight_base_year": 2026,
        },
    ]


def test_original_regimes_and_revisions(engine: Engine) -> None:
    """January and February-December are distinct published regimes, not revisions."""
    for rows, collected_at, expected in (
        (_rows(1000.0, 999.0), FIRST, (2, 0)),
        (_rows(1000.0, 999.0), FIRST, (0, 0)),
        (_rows(1000.0, 998.0), FIRST, (0, 0)),
        (_rows(1000.0, 997.0), LATER, (0, 1)),
    ):
        with engine.begin() as conn:
            assert upsert_original_weights(conn, rows, collected_at) == expected
    with engine.connect() as conn:
        stored = conn.execute(
            text(
                "SELECT weight, weight_base_year FROM collector_ons_ex_cpi.original_weights "
                "ORDER BY reference_date, vintage_date"
            )
        ).all()
    assert stored == [(1000.0, 2026), (998.0, 2026), (997.0, 2026)]
