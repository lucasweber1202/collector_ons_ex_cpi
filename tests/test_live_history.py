"""Opt-in historical MM23 snapshot audit from 2017 onward."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from scripts.special_aggregate_vintages import (
    DOUBLE_WEIGHT_START_YEAR,
    MM23Snapshot,
    collect_mm23_snapshot,
    discover_mm23_snapshots,
)
from scripts.special_aggregates import (
    EX_CPI_SPECIAL_AGGREGATES,
    MM23_WEIGHT_SUM_TOLERANCE,
    collect_mm23_special_aggregates,
)


def _scheduled_march_candidates(snapshots: list[MM23Snapshot], year: int) -> list[MM23Snapshot]:
    return [
        snapshot
        for snapshot in snapshots
        if snapshot.superseded_at.year == year
        and snapshot.superseded_at.month == 3
        and snapshot.reason == "scheduled"
    ]


def _weight_panel_error(values: dict[str, float] | None, year: int) -> str | None:
    if values is None:
        return f"annual weights for {year} missing"
    checked: set[str] = set()
    for aggregate in EX_CPI_SPECIAL_AGGREGATES:
        for cdid in (aggregate["weight_cdid"], aggregate["complement_weight_cdid"]):
            if cdid not in values:
                return f"reviewed CDID {cdid} missing"
            checked.add(cdid)
        residual = abs(
            values[aggregate["weight_cdid"]] + values[aggregate["complement_weight_cdid"]] - 1000.0
        )
        if residual > MM23_WEIGHT_SUM_TOLERANCE:
            return f"{aggregate['label']} residual {residual:.10f}"
    if len(checked) != 20:
        return f"expected 20 weight CDIDs, got {len(checked)}"
    return None


@pytest.mark.skipif(os.getenv("ONS_LIVE_TEST") != "1", reason="explicit live-source opt-in")
def test_historical_mm23_snapshot_matrix() -> None:
    current = collect_mm23_special_aggregates()
    snapshots = discover_mm23_snapshots()
    end_year = datetime.now(UTC).year
    failures: list[str] = []

    print(
        "| Year | January snapshot/version | Superseded date | Reason | Feb-Dec regime | Status |"
    )
    print("|---|---|---|---|---|---|")
    for year in range(DOUBLE_WEIGHT_START_YEAR, end_year + 1):
        candidates = _scheduled_march_candidates(snapshots, year)
        feb_dec_error = _weight_panel_error(current.annual_weights.get(year), year)
        if not candidates:
            status = "SOURCE GAP"
            snapshot_label = "missing"
            superseded = "-"
            reason = "no scheduled March snapshot"
        elif len(candidates) > 1:
            status = "FAIL"
            snapshot_label = ", ".join(snapshot.version_id for snapshot in candidates)
            superseded = ", ".join(
                snapshot.superseded_at.date().isoformat() for snapshot in candidates
            )
            reason = "ambiguous scheduled March selector"
        else:
            snapshot = candidates[0]
            snapshot_label = snapshot.version_id
            superseded = snapshot.superseded_at.date().isoformat()
            reason = snapshot.reason
            try:
                january = collect_mm23_snapshot(snapshot)
                january_error = _weight_panel_error(january.annual_weights.get(year), year)
            except Exception as exc:  # noqa: BLE001 - report every source failure
                january_error = f"snapshot unavailable/invalid: {exc}"
            errors = [error for error in (january_error, feb_dec_error) if error]
            status = "FAIL" if errors else "PASS"
            if errors:
                reason = "; ".join(errors)

        if status != "PASS":
            failures.append(f"{year}: {status} — {reason}")
        regime = f"MM23 annual {year}" if feb_dec_error is None else feb_dec_error
        print(f"| {year} | {snapshot_label} | {superseded} | {reason} | {regime} | {status} |")

    assert not failures, "\n".join(failures)
