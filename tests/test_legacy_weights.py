"""Reject unsafe in-place conversion of existing official baskets."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy.engine import Engine

from scripts import weights


def test_legacy_storage_is_blocked(engine: Engine) -> None:
    """Points per thousand and local shares must never share one column."""
    with engine.begin() as conn:
        weights.upsert_weights(
            conn,
            {date(2026, 1, 1): {"CPI_COICOP_ALL_D7BT": 1000.0}},
            datetime(2026, 8, 19),  # noqa: DTZ001
        )
    with engine.begin() as conn, pytest.raises(ValueError, match="legacy"):
        weights.assert_operational_storage(conn)


def test_operational_shares_pass_the_guard(engine: Engine) -> None:
    with engine.begin() as conn:
        weights.upsert_weights(
            conn,
            {date(2026, 1, 1): {"CPI_COICOP_ALL_D7BT": 1.0, "CPI_COICOP_D01_D7BU": 0.25}},
            datetime(2026, 8, 19),  # noqa: DTZ001
        )
    with engine.begin() as conn:
        weights.assert_operational_storage(conn)  # must not raise
