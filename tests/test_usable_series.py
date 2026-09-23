"""GUIDELINES 5.1 for the EX-CPI target.

Operational and original weights use their component index ID. The native
MM23 weight CDID remains auditable in weight_component_crosswalk.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

import pytest

from scripts.config import MIN_HISTORY_YEARS
from scripts.special_aggregates import EX_CPI_SPECIAL_AGGREGATES
from scripts.usable_series import apply_usable_series_filter, classify_series

LATEST = date(2026, 8, 1)
Points = Sequence[tuple[date, float | None]]

FIRST = EX_CPI_SPECIAL_AGGREGATES[0]
INDEX_ID = f"EXCPI_INDEX_NATIVE_{FIRST['index_cdid']}"
WEIGHT_ID = INDEX_ID


def _month_run(start: date, count: int) -> list[date]:
    out: list[date] = []
    year, month = start.year, start.month
    for _ in range(count):
        out.append(date(year, month, 1))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def _series(spec: Mapping[str, Points]) -> dict[date, dict[str, float | None]]:
    observations: dict[date, dict[str, float | None]] = {}
    for series_id, points in spec.items():
        for month, value in points:
            observations.setdefault(month, {})[series_id] = value
    return observations


def _weight_rows(reference_date: date = LATEST) -> list[dict[str, object]]:
    return [{"series_id": WEIGHT_ID, "reference_date": reference_date, "weight": 800.0}]


# -- the crosswalk protection ---------------------------------------------


def test_protection_uses_component_identity() -> None:
    """A current component weight protects its corresponding index."""
    assert INDEX_ID == WEIGHT_ID

    stale_index = _series({INDEX_ID: [(date(2025, 1, 1), 100.0)]})
    report = classify_series(stale_index, _weight_rows(), latest_period=LATEST)
    assert report.kept == (INDEX_ID,)
    assert report.protected_by_weight == (INDEX_ID,)
    assert report.dropped == ()


def test_a_retired_aggregate_is_dropped_when_its_weight_also_stops() -> None:
    """Retirement in MM23 means both sides stop, which is what 5.1 should catch."""
    stale_index = _series({INDEX_ID: [(date(2025, 1, 1), 100.0)]})
    stale_weights = _weight_rows(date(2025, 1, 1))
    report = classify_series(stale_index, stale_weights, latest_period=LATEST)
    assert report.stale == (INDEX_ID,)
    assert report.kept == ()


def test_no_weight_rows_at_all_leaves_nothing_protected() -> None:
    stale_index = _series({INDEX_ID: [(date(2025, 1, 1), 100.0)]})
    report = classify_series(stale_index, [], latest_period=LATEST)
    assert report.stale == (INDEX_ID,)


# -- the filter must not fire on legitimate aggregates ---------------------


def test_a_currently_printing_aggregate_is_kept() -> None:
    report = classify_series(
        _series({INDEX_ID: [(LATEST, 100.0)]}), _weight_rows(), latest_period=LATEST
    )
    assert report.kept == (INDEX_ID,)
    assert report.dropped == ()


def test_a_newly_introduced_aggregate_is_kept() -> None:
    """ONS may add an exclusion aggregate at any release; new is not unusable."""
    report = classify_series(_series({INDEX_ID: [(LATEST, 100.0)]}), [], latest_period=LATEST)
    assert report.kept == (INDEX_ID,)


def test_trailing_nulls_after_a_recent_print_do_not_drop_it() -> None:
    """Ample history so only staleness is under test.

    A two-point fixture would be a stub and would be dropped by the history
    rule for reasons unrelated to the behaviour this test names.
    """
    history: list[tuple[date, float | None]] = [
        (m, 100.0) for m in _month_run(date(2024, 8, 1), 24)
    ]
    spec: dict[str, list[tuple[date, float | None]]] = {INDEX_ID: [*history, (LATEST, None)]}
    report = classify_series(_series(spec), [], latest_period=LATEST)
    assert report.kept == (INDEX_ID,)


def test_padded_nulls_cannot_disguise_a_dead_aggregate() -> None:
    spec: dict[str, list[tuple[date, float | None]]] = {
        INDEX_ID: [(date(2025, 1, 1), 100.0), (date(2026, 7, 1), None), (LATEST, None)]
    }
    report = classify_series(_series(spec), [], latest_period=LATEST)
    assert report.stale == (INDEX_ID,)


def test_an_all_null_series_is_empty_not_stale() -> None:
    spec: dict[str, list[tuple[date, float | None]]] = {INDEX_ID: [(LATEST, None)]}
    report = classify_series(_series(spec), [], latest_period=LATEST)
    assert report.empty == (INDEX_ID,)


@pytest.mark.parametrize(
    ("last_print", "kept"), [(date(2026, 2, 1), True), (date(2026, 1, 1), False)]
)
def test_the_staleness_boundary_is_inclusive(last_print: date, kept: bool) -> None:
    """25 months inclusive so the run ends exactly on last_print."""
    span_start = date(last_print.year - 2, last_print.month, 1)
    spec = {INDEX_ID: [(m, 100.0) for m in _month_run(span_start, 25)]}
    report = classify_series(_series(spec), [], latest_period=LATEST, max_stale_months=6)
    assert (report.kept == (INDEX_ID,)) is kept


# -- pruning semantics -----------------------------------------------------


def test_only_the_judged_unusable_are_removed() -> None:
    """Not 'retain only the classified' -- that deletes unclassified rows."""
    live = f"EXCPI_INDEX_NATIVE_{EX_CPI_SPECIAL_AGGREGATES[1]['index_cdid']}"
    observations = _series({INDEX_ID: [(date(2025, 1, 1), 100.0)], live: [(LATEST, 100.0)]})
    catalog = {INDEX_ID: {"name": "dead"}, live: {"name": "live"}}
    obs, cat, report = apply_usable_series_filter(observations, catalog, [], latest_period=LATEST)
    assert report.stale == (INDEX_ID,)
    surviving = {sid for values in obs.values() for sid in values}
    assert surviving == {live}
    assert set(cat) == {live}


def test_all_ten_native_aggregates_survive_a_healthy_release() -> None:
    """The regression guard: a healthy release must lose nothing."""
    healthy = {
        f"EXCPI_INDEX_NATIVE_{a['index_cdid']}": [(LATEST, 100.0)]
        for a in EX_CPI_SPECIAL_AGGREGATES
    }
    _obs, cat, report = apply_usable_series_filter(
        _series(healthy), {sid: {} for sid in healthy}, [], latest_period=LATEST
    )
    assert len(report.kept) == 10
    assert report.dropped == ()
    assert set(cat) == set(healthy)


# -- 5.1's second half: insufficient history -------------------------------


def _run(start: date, count: int) -> list[tuple[date, float | None]]:
    return [(m, 100.0) for m in _month_run(start, count)]


def test_the_minimum_history_threshold_is_a_real_number() -> None:
    """A symbolic zero would satisfy the letter of 5.1 and none of its point."""
    assert MIN_HISTORY_YEARS > 0


def test_a_short_stub_aggregate_that_stopped_is_dropped() -> None:
    """Too few prints, stopped too recently for staleness to reach it."""
    stub = {INDEX_ID: _run(date(2026, 3, 1), 3)}
    report = classify_series(_series(stub), [], latest_period=LATEST)
    assert report.short_history == (INDEX_ID,)
    assert report.stale == ()
    assert report.kept == ()


def test_a_newly_introduced_aggregate_is_not_mistaken_for_a_stub() -> None:
    """Same short span, but still printing -- ONS may add an aggregate anytime."""
    fresh = {INDEX_ID: _run(date(2026, 6, 1), 3)}
    assert fresh[INDEX_ID][-1][0] == LATEST
    report = classify_series(_series(fresh), [], latest_period=LATEST)
    assert report.kept == (INDEX_ID,)
    assert report.short_history == ()


def test_component_weight_protection_outranks_the_history_rule() -> None:
    """A short, stopped aggregate whose weight is still current is kept.

    A current operational weight protects an index that stopped printing.
    """
    short_stopped = {INDEX_ID: _run(date(2026, 3, 1), 3)}
    report = classify_series(_series(short_stopped), _weight_rows(), latest_period=LATEST)
    assert report.kept == (INDEX_ID,)
    assert report.short_history == ()
    assert report.protected_by_weight == ()


def test_a_retired_aggregate_with_a_retired_weight_is_dropped() -> None:
    """Both sides gone: index stopped long ago and the weight stopped with it."""
    retired = {INDEX_ID: _run(date(2024, 1, 1), 12)}
    report = classify_series(
        _series(retired), _weight_rows(date(2024, 12, 1)), latest_period=LATEST
    )
    assert report.stale == (INDEX_ID,)


def test_a_long_history_aggregate_that_stopped_recently_is_not_called_short() -> None:
    long_stopped = {INDEX_ID: _run(date(2024, 1, 1), 29)}
    report = classify_series(_series(long_stopped), [], latest_period=LATEST)
    assert report.kept == (INDEX_ID,)
    assert report.short_history == ()


def test_all_ten_aggregates_survive_a_healthy_release_with_history_rule_on() -> None:
    """The regression guard, re-stated with both halves of 5.1 active."""
    healthy = {
        f"EXCPI_INDEX_NATIVE_{a['index_cdid']}": _run(date(2026, 6, 1), 3)
        for a in EX_CPI_SPECIAL_AGGREGATES
    }
    _obs, cat, report = apply_usable_series_filter(
        _series(healthy), {sid: {} for sid in healthy}, [], latest_period=LATEST
    )
    assert len(report.kept) == 10
    assert report.dropped == ()
    assert set(cat) == set(healthy)
