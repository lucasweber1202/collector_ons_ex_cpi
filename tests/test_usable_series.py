"""GUIDELINES 5.1 for the EX-CPI target.

The protection here is crosswalk-based rather than same-identity, because an
index is stored as EXCPI_INDEX_NATIVE_<cdid> while its weight is
EXCPI_WEIGHT_NATIVE_<a different cdid>. Copying the CPI collector's check would
silently protect nothing, so the first test below pins exactly that.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

import pytest

from scripts.special_aggregates import EX_CPI_SPECIAL_AGGREGATES
from scripts.usable_series import apply_usable_series_filter, classify_series

LATEST = date(2026, 8, 1)
Points = Sequence[tuple[date, float | None]]

FIRST = EX_CPI_SPECIAL_AGGREGATES[0]
INDEX_ID = f"EXCPI_INDEX_NATIVE_{FIRST['index_cdid']}"
WEIGHT_ID = f"EXCPI_WEIGHT_NATIVE_{FIRST['weight_cdid']}"


def _series(spec: Mapping[str, Points]) -> dict[date, dict[str, float | None]]:
    observations: dict[date, dict[str, float | None]] = {}
    for series_id, points in spec.items():
        for month, value in points:
            observations.setdefault(month, {})[series_id] = value
    return observations


def _weight_rows(reference_date: date = LATEST) -> list[dict[str, object]]:
    return [{"series_id": WEIGHT_ID, "reference_date": reference_date, "weight": 800.0}]


# -- the crosswalk protection ---------------------------------------------


def test_protection_is_crosswalk_based_not_same_identity() -> None:
    """The index and its weight never share an id, so this must resolve them.

    A same-identity check -- the shape CPI uses -- would find no match here and
    leave a live aggregate unprotected. This test fails if anyone replaces the
    crosswalk lookup with one.
    """
    assert INDEX_ID != WEIGHT_ID
    assert FIRST["index_cdid"] not in WEIGHT_ID

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
    spec: dict[str, list[tuple[date, float | None]]] = {
        INDEX_ID: [(date(2026, 7, 1), 100.0), (LATEST, None)]
    }
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
    report = classify_series(
        _series({INDEX_ID: [(last_print, 100.0)]}), [], latest_period=LATEST, max_stale_months=6
    )
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
