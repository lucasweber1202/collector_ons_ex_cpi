"""Ground-truth regression tests for the ONS price-updated bottom-up formula.

Each case builds a parent index from its children with the published ONS rule
(Laspeyres-type aggregation against an annual January price reference, chained
in December), so a correct reconciliation must return a residual of zero. Any
non-zero residual here is an implementation defect, not source methodology.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from scripts.validate import build_hierarchy, validate_bottom_up
from tests.conftest import catalog_entry

_PARENT = catalog_entry("COICOP", "ALL", "D7BT", "All items", level="all_items")
_RISING = catalog_entry(
    "COICOP", "D01", "D7BU", "Rising division", level="division", parent=_PARENT[0]
)
_FLAT = catalog_entry("COICOP", "D02", "D7BV", "Flat division", level="division", parent=_PARENT[0])
PARENT, CHILD_RISING, CHILD_FLAT = _PARENT[0], _RISING[0], _FLAT[0]
HIERARCHY = build_hierarchy(dict([_PARENT, _RISING, _FLAT]))

WEIGHT_RISING = 400.0
WEIGHT_FLAT = 600.0
EXACT = 1e-10


def _child_levels(year: int, january_level: float, monthly_factor: float) -> dict[date, float]:
    """Roll one child index forward from its January level at a fixed rate."""
    levels = {date(year, 1, 1): january_level}
    for month in range(2, 13):
        levels[date(year, month, 1)] = levels[date(year, month - 1, 1)] * monthly_factor
    return levels


def _ons_parent(
    rising: dict[date, float],
    flat: dict[date, float],
    reference: date,
    parent_at_reference: float,
    weights: tuple[float, float],
) -> dict[date, float]:
    """Aggregate a parent with ONS Formula 1 against a fixed price reference month."""
    weight_rising, weight_flat = weights
    total = weight_rising + weight_flat
    parent: dict[date, float] = {}
    for month, rising_level in rising.items():
        relative = (
            weight_rising * rising_level / rising[reference]
            + weight_flat * flat[month] / flat[reference]
        ) / total
        parent[month] = parent_at_reference * relative
    return parent


def _observations(
    rising: dict[date, float], flat: dict[date, float], parent: dict[date, float]
) -> dict[date, dict[str, float | None]]:
    return {
        month: {PARENT: parent[month], CHILD_RISING: rising[month], CHILD_FLAT: flat[month]}
        for month in sorted(rising)
    }


def _annual_weights(year: int, months: range) -> dict[date, dict[str, float]]:
    return {
        date(year, month, 1): {
            PARENT: WEIGHT_RISING + WEIGHT_FLAT,
            CHILD_RISING: WEIGHT_RISING,
            CHILD_FLAT: WEIGHT_FLAT,
        }
        for month in months
    }


@pytest.mark.parametrize(
    ("january_rising", "january_flat"),
    [
        # Both children on the same January level: the price-updating term is
        # invisible here, which is why this case alone cannot discriminate the
        # ONS reference from a plain I(t-1) reference.
        (100.0, 100.0),
        # Different January levels, as in Table 38 (2015=100, never re-referenced
        # to January). Only the ONS January price reference reconciles this.
        (85.0, 130.0),
    ],
)
def test_bottom_up_reconciles_ons_constructed_parent(
    january_rising: float, january_flat: float
) -> None:
    """A parent built by the ONS rule must reconcile exactly in every month."""
    rising = _child_levels(2026, january_rising, 1.03)
    flat = _child_levels(2026, january_flat, 1.0)
    parent = _ons_parent(rising, flat, date(2026, 1, 1), 100.0, (WEIGHT_RISING, WEIGHT_FLAT))
    results, skips = validate_bottom_up(
        _observations(rising, flat, parent),
        _annual_weights(2026, range(1, 13)),
        HIERARCHY,
        tolerance_pp=EXACT,
    )

    assert skips == Counter()

    assert len(results) == 11  # February through December
    for row in results:
        assert row["residual"] == pytest.approx(0.0, abs=EXACT), (
            f"{row['reference_date']} residual {row['residual']:.10f} pp"
        )
        assert row["passed"] is True


def test_bottom_up_reconciles_december_to_january_chain_link() -> None:
    """The January link chains on December, including across a weight regime change."""
    rising = _child_levels(2025, 85.0, 1.03)
    flat = _child_levels(2025, 130.0, 1.001)
    parent = _ons_parent(rising, flat, date(2025, 1, 1), 100.0, (WEIGHT_RISING, WEIGHT_FLAT))

    # January of the next year re-weights against December price references.
    january_rising_weight, january_flat_weight = 450.0, 550.0
    next_january = date(2026, 1, 1)
    rising[next_january] = rising[date(2025, 12, 1)] * 1.02
    flat[next_january] = flat[date(2025, 12, 1)] * 0.999
    link = (
        january_rising_weight * rising[next_january] / rising[date(2025, 12, 1)]
        + january_flat_weight * flat[next_january] / flat[date(2025, 12, 1)]
    ) / (january_rising_weight + january_flat_weight)
    parent[next_january] = parent[date(2025, 12, 1)] * link

    weights = _annual_weights(2025, range(1, 13))
    weights[next_january] = {
        PARENT: january_rising_weight + january_flat_weight,
        CHILD_RISING: january_rising_weight,
        CHILD_FLAT: january_flat_weight,
    }

    results, skips = validate_bottom_up(
        _observations(rising, flat, parent), weights, HIERARCHY, tolerance_pp=EXACT
    )

    assert skips == Counter()

    january = next(row for row in results if row["reference_date"] == next_january)
    assert january["residual"] == pytest.approx(0.0, abs=EXACT)
    assert january["passed"] is True
