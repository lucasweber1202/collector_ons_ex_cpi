"""Opt-in ONS source replay against a real SQLite test database.

Run: ONS_LIVE_TEST=1 python -m pytest tests/test_live_source.py -q -s
No production database is contacted and no downloaded workbook is cached.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

import main
from scripts import extract, segments
from scripts.special_aggregate_rates import published_12m_rate_checks
from scripts.special_aggregate_vintages import (
    build_exclusion_weight_regimes,
    collect_mm23_snapshot,
    discover_mm23_snapshots,
    january_regime_snapshots,
)
from scripts.special_aggregates import (
    EX_CPI_SPECIAL_AGGREGATES,
    MM23SpecialPanel,
    collect_mm23_special_aggregates,
    complement_weight_checks,
    resolve_table38_alt_series,
)


@pytest.mark.skipif(os.getenv("ONS_LIVE_TEST") != "1", reason="explicit live-source opt-in")
def test_source_replay_twice_and_logged_failure(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    observations = extract.collect_raw_data(date(1988, 1, 1))
    basket = extract.collect_weights(date(2008, 1, 1))
    catalog = extract.get_series_catalog()
    release_date = extract.get_last_publish_date()
    assert release_date is not None

    # The ex-CPI layer is a reviewed MM23 identity map onto the Table 38 ALT
    # series already collected here. A missing CDID means ONS scope changed and
    # must be reviewed instead of silently creating or fuzzy-matching a series.
    resolved_ex_cpi, missing_ex_cpi = resolve_table38_alt_series(catalog)
    assert not missing_ex_cpi, f"MM23 ex-CPI CDIDs missing from Table 38 ALT: {missing_ex_cpi}"
    assert set(resolved_ex_cpi) == {
        row["index_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES
    }

    # MM23 is validation-only at this stage: prove the reviewed columns still
    # exist, reconcile each latest exclusion/complement weight pair, and compare
    # the Table 38 12-month change with MM23's independently published rate.
    # Nothing from this panel is persisted here.
    mm23 = collect_mm23_special_aggregates()
    special_weight_checks = complement_weight_checks(mm23, latest_only=True)
    assert len(special_weight_checks) == len(EX_CPI_SPECIAL_AGGREGATES)
    failed_special_weights = [check for check in special_weight_checks if not check["passed"]]
    assert not failed_special_weights, (
        f"MM23 complement weights do not sum to 1000: {failed_special_weights}"
    )

    special_rate_checks = published_12m_rate_checks(
        observations,
        catalog,
        mm23,
        latest_only=True,
    )
    assert len(special_rate_checks) == len(EX_CPI_SPECIAL_AGGREGATES)
    failed_special_rates = [check for check in special_rate_checks if not check["passed"]]
    assert not failed_special_rates, (
        f"Table 38 levels do not reconcile with MM23 12m rates: {failed_special_rates}"
    )

    # From 2017 onward, the final January regime is the full-MM23 version
    # superseded by the scheduled March release. In February of the current year
    # that archive does not exist yet because the current MM23 file *is* the
    # January regime. The test handles both legitimate states explicitly.
    weight_year = max(mm23.annual_weights)
    january_panels: dict[int, MM23SpecialPanel] = {}
    january_snapshot_id = "current-February-release"
    archived = january_regime_snapshots(discover_mm23_snapshots())
    if weight_year in archived:
        january_snapshot = archived[weight_year]
        assert january_snapshot.reason == "scheduled"
        assert january_snapshot.superseded_at.month == 3
        january_mm23 = collect_mm23_snapshot(january_snapshot)
        assert weight_year in january_mm23.annual_weights
        january_weight_checks = complement_weight_checks(january_mm23, latest_only=True)
        assert len(january_weight_checks) == len(EX_CPI_SPECIAL_AGGREGATES)
        failed_january_weights = [
            check for check in january_weight_checks if not check["passed"]
        ]
        assert not failed_january_weights, (
            f"Archived January MM23 complement weights do not sum to 1000: "
            f"{failed_january_weights}"
        )
        january_panels[weight_year] = january_mm23
        january_snapshot_id = january_snapshot.version_id
    else:
        assert release_date.year == weight_year and release_date.month == 2

    special_weight_regimes = build_exclusion_weight_regimes(
        mm23,
        release_date,
        january_panels,
        start_year=weight_year,
    )
    assert date(weight_year, 1, 1) in special_weight_regimes
    if release_date.year > weight_year or release_date.month >= 3:
        assert date(weight_year, 12, 1) in special_weight_regimes
    else:
        assert set(special_weight_regimes) == {date(weight_year, 1, 1)}

    weight_codes = [fields["code"] for fields in extract.get_original_weight_catalog().values()]
    panel = segments.collect_segments(date(1988, 1, 1), catalog, weight_codes)

    monkeypatch.setattr(main, "_preflight", lambda: None)
    monkeypatch.setattr(main, "build_engine", lambda: engine)
    monkeypatch.setattr(main, "init_db", lambda _engine: None)
    monkeypatch.setattr(main, "collect_raw_data", lambda _start: observations)
    monkeypatch.setattr(main, "collect_weights", lambda _start: basket)
    monkeypatch.setattr(main, "collect_segments", lambda *_args: panel)

    def snapshot() -> dict[str, list[tuple[Any, ...]]]:
        """Read every persisted row so a rerun can be compared field by field."""
        ordering = {
            "time_series": "series_id, reference_date, vintage_date",
            "weights": "series_id, reference_date, vintage_date",
            "original_weights": "series_id, reference_date, vintage_date",
            "metadata": "series_id",
        }
        with engine.connect() as conn:
            return {
                table: [
                    tuple(row)
                    for row in conn.execute(
                        text(f"SELECT * FROM collector_ons_ex_cpi.{table} ORDER BY {order}")
                    ).all()
                ]
                for table, order in ordering.items()
            }

    assert main.run(["--no-watch"]) == 0
    first = snapshot()
    assert main.run(["--no-watch"]) == 0
    assert snapshot() == first

    stored_by_series: dict[str, dict[date, float]] = {}
    for series_id, reference_date, _vintage, value, _collected in first["time_series"]:
        stored_by_series.setdefault(str(series_id), {})[reference_date] = float(value)
    source: dict[str, dict[date, float]] = {
        series_id: {
            month: float(value)
            for month, values in sorted(observations.items())
            if (value := values.get(series_id)) is not None
        }
        for series_id in catalog
    }
    source |= {
        series_id: {
            month: float(values[series_id])
            for month, values in sorted(panel.observations.items())
            if series_id in values
        }
        for series_id in panel.catalog
    }
    assert set(stored_by_series) == set(source)
    for series_id, published in source.items():
        stored = stored_by_series[series_id]
        assert len(stored) == len(published)
        months = sorted(published)
        for month in (months[0], months[len(months) // 2], months[-1]):
            assert stored[month] == published[month]

    monkeypatch.setattr(main, "collect_weights", lambda _start: {})
    assert main.run(["--no-watch"]) == 1
    assert snapshot() == first
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT status FROM collector_ons_ex_cpi.logs ORDER BY id")
        ).scalars().all() == ["success", "success", "error"]
    print(
        "SOURCE REPLAY",
        max(observations),
        {table: len(rows) for table, rows in first.items()},
        f"segments={len(panel.catalog)}",
        f"ex_cpi={len(resolved_ex_cpi)}",
        f"ex_cpi_rates={len(special_rate_checks)}",
        f"january_snapshot={january_snapshot_id}",
    )
