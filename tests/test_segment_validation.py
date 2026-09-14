"""Ground-truth tests for the consumption-segment reconciliation and weights.

Each parent is constructed from its segments with the ONS rule the collector
claims to reproduce, so a correct implementation must return a zero residual.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from scripts.validate import (
    SKIP_SEGMENT_CHAIN_LINK,
    SKIP_SEGMENT_COMPOSITION,
    derive_segment_operational_weights,
    log_validation_summary,
    validate_segment_bottom_up,
    validate_segment_weight_sums,
)

PARENT = "CPI_COICOP_C0111_D7D5"
FIRST = "CPI_CS_SEG_CP0111301"
SECOND = "CPI_CS_SEG_CP0111302"
EXACT = 1e-10

MARCH, APRIL, MAY = date(2026, 3, 1), date(2026, 4, 1), date(2026, 5, 1)
WEIGHTS = {FIRST: 12.0, SECOND: 8.0}


def _panel(levels: dict[date, tuple[float, float]]) -> dict[date, dict[str, float | None]]:
    """Build segment levels and the parent the ONS rule implies from them."""
    months = sorted(levels)
    base = months[0]
    observations: dict[date, dict[str, float | None]] = {}
    parent_level = 140.0
    for index, month in enumerate(months):
        first, second = levels[month]
        if index:
            previous_first, previous_second = levels[months[index - 1]]
            numerator = WEIGHTS[FIRST] * first + WEIGHTS[SECOND] * second
            denominator = WEIGHTS[FIRST] * previous_first + WEIGHTS[SECOND] * previous_second
            parent_level *= numerator / denominator
        observations[month] = {PARENT: parent_level, FIRST: first, SECOND: second}
    assert base in observations
    return observations


def _hierarchy(months: list[date]) -> dict[date, dict[str, list[str]]]:
    return {month: {PARENT: [FIRST, SECOND]} for month in months}


def _weights(months: list[date]) -> dict[date, dict[str, float]]:
    return {month: dict(WEIGHTS) for month in months}


def test_segments_reconstruct_their_published_parent_exactly() -> None:
    """Segment indices share one January reference, so the link is a plain ratio."""
    months = [MARCH, APRIL, MAY]
    observations = _panel({MARCH: (103.0, 99.0), APRIL: (104.5, 98.2), MAY: (106.1, 97.9)})
    results, skips = validate_segment_bottom_up(
        observations, _weights(months), _hierarchy(months), tolerance_pp=EXACT
    )
    assert skips == Counter()
    assert len(results) == 2
    for row in results:
        assert row["residual"] == pytest.approx(0.0, abs=EXACT)
        assert row["passed"] is True


def test_a_material_mismatch_is_flagged() -> None:
    months = [MARCH, APRIL]
    observations = _panel({MARCH: (100.0, 100.0), APRIL: (100.0, 100.0)})
    march_parent = observations[MARCH][PARENT]
    assert march_parent is not None
    observations[APRIL][PARENT] = march_parent * 1.05
    results, _ = validate_segment_bottom_up(
        observations, _weights(months), _hierarchy(months), tolerance_pp=0.5
    )
    assert results[0]["passed"] is False
    assert results[0]["residual"] == pytest.approx(-5.0)


@pytest.mark.parametrize(
    ("previous", "current"),
    [(date(2025, 12, 1), date(2026, 1, 1)), (date(2026, 1, 1), date(2026, 2, 1))],
)
def test_links_touching_january_are_reported_unreconcilable(previous: date, current: date) -> None:
    """The January re-reference makes those two links undefined at source."""
    months = [previous, current]
    observations = _panel({previous: (105.0, 104.0), current: (99.5, 100.2)})
    results, skips = validate_segment_bottom_up(observations, _weights(months), _hierarchy(months))
    assert results == []
    assert skips == Counter({SKIP_SEGMENT_CHAIN_LINK: 1})
    # Unreconcilable at source must not depress coverage, and must not be
    # mistaken for a clean run either.
    _, _, _, coverage, _, attempted = log_validation_summary(results, skips, "Segment")
    assert (coverage, attempted) == (0.0, 0)


def test_a_changed_segment_set_is_not_silently_linked() -> None:
    months = [MARCH, APRIL]
    observations = _panel({MARCH: (103.0, 99.0), APRIL: (104.5, 98.2)})
    hierarchy = _hierarchy(months)
    hierarchy[MARCH] = {PARENT: [FIRST]}
    results, skips = validate_segment_bottom_up(observations, _weights(months), hierarchy)
    assert results == []
    assert skips == Counter({SKIP_SEGMENT_COMPOSITION: 1})


def test_stored_operational_shares_reproduce_the_parent_without_a_second_update() -> None:
    months = [MARCH, APRIL, MAY]
    observations = _panel({MARCH: (103.0, 99.0), APRIL: (104.5, 98.2), MAY: (106.1, 97.9)})
    hierarchy = _hierarchy(months)
    operational = derive_segment_operational_weights(observations, _weights(months), hierarchy)
    april = operational[APRIL]
    assert april[FIRST] + april[SECOND] == pytest.approx(1.0)
    assert april[FIRST] == pytest.approx(
        WEIGHTS[FIRST] * 103.0 / (WEIGHTS[FIRST] * 103.0 + WEIGHTS[SECOND] * 99.0)
    )
    results, _ = validate_segment_bottom_up(
        observations, operational, hierarchy, tolerance_pp=EXACT, operational=True
    )
    assert len(results) == 2
    for row in results:
        assert row["residual"] == pytest.approx(0.0, abs=EXACT)


def test_no_operational_share_is_invented_across_the_january_reset() -> None:
    months = [date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)]
    observations = _panel(
        {months[0]: (105.0, 104.0), months[1]: (99.5, 100.2), months[2]: (101.1, 100.8)}
    )
    operational = derive_segment_operational_weights(
        observations, _weights(months), _hierarchy(months)
    )
    assert set(operational) == set()


def test_segment_weights_may_under_cover_but_never_over_cover_the_parent() -> None:
    """ONS builds actual rentals partly from sources it does not publish."""
    basket = {MARCH: {PARENT: 21.0}}
    results, _ = validate_segment_weight_sums(_weights([MARCH]), basket, _hierarchy([MARCH]))
    assert results[0]["passed"] is True
    assert results[0]["residual"] == pytest.approx(-1.0)

    over = {MARCH: {PARENT: 19.0}}
    results, _ = validate_segment_weight_sums(_weights([MARCH]), over, _hierarchy([MARCH]))
    assert results[0]["passed"] is False


def test_under_coverage_beyond_the_floor_fails() -> None:
    basket = {MARCH: {PARENT: 40.0}}
    results, _ = validate_segment_weight_sums(_weights([MARCH]), basket, _hierarchy([MARCH]))
    assert results[0]["passed"] is False
    assert results[0]["reconstructed"] == pytest.approx(20.0)
