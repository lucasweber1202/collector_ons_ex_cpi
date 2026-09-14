"""Opt-in historical MM23 snapshot audit from 2017 onward."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

import pytest

from scripts.special_aggregate_vintages import (
    DOUBLE_WEIGHT_START_YEAR,
    collect_mm23_snapshot,
    discover_mm23_snapshots,
    january_regime_snapshots,
)
from scripts.special_aggregates import (
    EX_CPI_SPECIAL_AGGREGATES,
    MM23_WEIGHT_SUM_TOLERANCE,
    collect_mm23_special_aggregates,
)


@pytest.mark.skipif(os.getenv("ONS_LIVE_TEST") != "1", reason="explicit live-source opt-in")
def test_historical_mm23_snapshot_matrix() -> None:
    current = collect_mm23_special_aggregates()
    selected = january_regime_snapshots(discover_mm23_snapshots())
    end_year = datetime.now(UTC).year
    expected = set(range(DOUBLE_WEIGHT_START_YEAR, end_year + 1))
    assert set(selected) >= expected, f'Missing scheduled-March snapshots: {sorted(expected - set(selected))}'
    print("| Year | January snapshot/version | Superseded date | Reason | Feb-Dec regime | Status |")
    print("|---|---|---|---|---|---|")
    for year in sorted(expected):
        snapshot = selected[year]
        january = collect_mm23_snapshot(snapshot)
        january_weights = january.annual_weights.get(year)
        feb_dec_weights = current.annual_weights.get(year)
        assert january_weights is not None, f'{snapshot.version_id} has no {year} annual weights'
        assert feb_dec_weights is not None, f'Current MM23 has no {year} annual weights'
        checked_cdids: set[str] = set()
        for aggregate in EX_CPI_SPECIAL_AGGREGATES:
            for values in (january_weights, feb_dec_weights):
                exclusion = values[aggregate['weight_cdid']]
                complement = values[aggregate['complement_weight_cdid']]
                assert abs(exclusion + complement - 1000.0) <= MM23_WEIGHT_SUM_TOLERANCE
            checked_cdids.add(aggregate['weight_cdid'])
            checked_cdids.add(aggregate['complement_weight_cdid'])
        assert len(checked_cdids) == 20
        print(
            f"| {year} | {snapshot.version_id} | {snapshot.superseded_at.date()} | "
            f"{snapshot.reason} | MM23 annual {year} | PASS |"
        )
