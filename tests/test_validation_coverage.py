"""Regression tests for validation skip accounting and coverage reporting."""

from __future__ import annotations

from collections import Counter
from datetime import date

from scripts.validate import (
    SKIP_CHILD_WITHOUT_OBSERVATION,
    SKIP_CHILD_WITHOUT_WEIGHT,
    SKIP_OUTSIDE_WEIGHTS_WINDOW,
    SKIP_PARENT_WITHOUT_WEIGHT,
    build_hierarchy,
    log_validation_summary,
    validate_bottom_up,
    validate_weight_sums,
)
from tests.conftest import catalog_entry

_PARENT = catalog_entry("COICOP", "ALL", "D7BT", "All items", level="all_items")
_FIRST = catalog_entry(
    "COICOP", "D01", "D7BU", "First division", level="division", parent=_PARENT[0]
)
_SECOND = catalog_entry(
    "COICOP", "D02", "D7BV", "Second division", level="division", parent=_PARENT[0]
)
PARENT, FIRST, SECOND = _PARENT[0], _FIRST[0], _SECOND[0]
CATALOG = dict([_PARENT, _FIRST, _SECOND])
HIERARCHY = build_hierarchy(CATALOG)

JANUARY = date(2026, 1, 1)
FEBRUARY = date(2026, 2, 1)
OBSERVATIONS: dict[date, dict[str, float | None]] = {
    JANUARY: {PARENT: 100.0, FIRST: 100.0, SECOND: 100.0},
    FEBRUARY: {PARENT: 102.5, FIRST: 110.0, SECOND: 100.0},
}


def test_missing_child_weight_is_counted_not_silently_skipped() -> None:
    """A parent whose child has no weight must be reported, not dropped."""
    weights = {FEBRUARY: {PARENT: 1000.0, FIRST: 250.0}}

    results, skips = validate_bottom_up(OBSERVATIONS, weights, HIERARCHY)

    assert results == []
    assert skips == Counter({SKIP_CHILD_WITHOUT_WEIGHT: 1})


def test_missing_child_observation_is_counted_separately() -> None:
    """A missing observation and a missing weight are distinguishable reasons."""
    observations: dict[date, dict[str, float | None]] = {
        JANUARY: dict(OBSERVATIONS[JANUARY]),
        FEBRUARY: {**OBSERVATIONS[FEBRUARY], SECOND: None},
    }
    weights = {FEBRUARY: {PARENT: 1000.0, FIRST: 250.0, SECOND: 750.0}}

    results, skips = validate_bottom_up(observations, weights, HIERARCHY)

    assert results == []
    assert skips == Counter({SKIP_CHILD_WITHOUT_OBSERVATION: 1})


def test_weight_sums_counts_parent_and_child_gaps() -> None:
    """validate_weight_sums reports why a parent could not be reconciled."""
    weights = {
        JANUARY: {FIRST: 250.0, SECOND: 750.0},  # parent weight absent
        FEBRUARY: {PARENT: 1000.0, FIRST: 250.0},  # child weight absent
    }

    results, skips = validate_weight_sums(weights, HIERARCHY)

    assert results == []
    assert skips == Counter({SKIP_PARENT_WITHOUT_WEIGHT: 1, SKIP_CHILD_WITHOUT_WEIGHT: 1})


def test_summary_reports_zero_coverage_when_nothing_was_checked() -> None:
    """A run that reconciles nothing must not look like a clean run."""
    weights = {FEBRUARY: {PARENT: 1000.0, FIRST: 250.0}}
    results, skips = validate_bottom_up(OBSERVATIONS, weights, HIERARCHY)

    passed, failed, skipped, coverage, max_residual, attempted = log_validation_summary(
        results, skips, "Bottom-up"
    )
    assert attempted == 1

    assert (passed, failed) == (0, 0)
    assert skipped == 1
    assert coverage == 0.0
    assert max_residual == 0.0


def test_recent_missing_weight_month_depresses_coverage() -> None:
    """A missing recent month is a defect, not a source-window exception."""
    march = date(2026, 3, 1)
    observations: dict[date, dict[str, float | None]] = {
        **OBSERVATIONS,
        march: {PARENT: 103.0, FIRST: 112.0, SECOND: 100.0},
    }
    weights = {march: {PARENT: 1000.0, FIRST: 250.0, SECOND: 750.0}}

    results, skips = validate_bottom_up(observations, weights, HIERARCHY)
    _, _, skipped, coverage, _, _ = log_validation_summary(results, skips, "Bottom-up")

    assert len(results) == 1  # only March carries weights
    assert skips[SKIP_OUTSIDE_WEIGHTS_WINDOW] == 0  # 2026 is within source coverage
    assert skipped == 1
    assert coverage == 0.5


def test_only_actual_pre_2008_months_are_exempt() -> None:
    observations = {
        date(2007, 1, 1): OBSERVATIONS[JANUARY],
        date(2007, 2, 1): OBSERVATIONS[FEBRUARY],
    }
    results, skips = validate_bottom_up(observations, {}, HIERARCHY)
    assert not results
    assert skips == Counter({SKIP_OUTSIDE_WEIGHTS_WINDOW: 1})


def test_full_coverage_reports_one() -> None:
    """Every reconcilable check running reports coverage 1.0."""
    weights = {FEBRUARY: {PARENT: 1000.0, FIRST: 250.0, SECOND: 750.0}}

    results, skips = validate_bottom_up(OBSERVATIONS, weights, HIERARCHY)
    _, _, skipped, coverage, _, _ = log_validation_summary(results, skips, "Bottom-up")

    assert len(results) == 1
    assert skipped == 0
    assert coverage == 1.0
