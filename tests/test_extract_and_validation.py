"""Focused regression tests for structured IDs, hierarchy, and CPI reconciliation."""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from scripts.extract import _node_token, _weight_header, make_series_id, parse_series_id
from scripts.validate import build_hierarchy, validate_bottom_up, validate_weight_sums
from tests.conftest import catalog_entry


def test_series_id_round_trip_and_node_normalization() -> None:
    series_id = make_series_id("COICOP", "D01", "D7BU")
    assert parse_series_id(series_id) == ("CPI", "COICOP", "D01", "D7BU")
    assert _node_token("01.1") == ("COICOP", "G011", "group")
    assert _node_token("Agg2") == ("ALT", "A02", "analytical_aggregate")
    assert _node_token("0") == ("COICOP", "ALL", "all_items")
    assert _node_token(1) == ("COICOP", "D01", "division")
    assert _node_token("01.1.1") == ("COICOP", "C0111", "class")


def test_weight_header_preserves_double_weight_regimes() -> None:
    assert _weight_header("2026 Jan") == (2026, (1,))
    assert _weight_header("2026 Feb-Dec") == (2026, tuple(range(2, 13)))
    assert _weight_header(2016) == (2016, tuple(range(1, 13)))


def _catalog() -> dict[str, dict[str, str]]:
    all_items = catalog_entry("COICOP", "ALL", "D7BT", "All items", level="all_items")
    first = catalog_entry(
        "COICOP", "D01", "D7BU", "First division", level="division", parent=all_items[0]
    )
    second = catalog_entry(
        "COICOP", "D02", "D7BV", "Second division", level="division", parent=all_items[0]
    )
    alternative = catalog_entry("ALT", "A02", "D7F4", "All goods", level="analytical_aggregate")
    return dict([all_items, first, second, alternative])


CATALOG = _catalog()
ALL_ITEMS = "CPI_COICOP_ALL_D7BT"
FIRST = "CPI_COICOP_D01_D7BU"
SECOND = "CPI_COICOP_D02_D7BV"


def test_hierarchy_comes_from_the_upstream_parent_links() -> None:
    """The tree is read from official classification, not re-derived downstream."""
    assert build_hierarchy(CATALOG) == {ALL_ITEMS: [FIRST, SECOND]}


def test_unknown_parent_link_is_rejected() -> None:
    catalog = dict(CATALOG)
    catalog[FIRST] = {**catalog[FIRST], "parent_series_id": "CPI_COICOP_ALL_MISSING"}
    with pytest.raises(ValueError, match="unknown parent"):
        build_hierarchy(catalog)


def test_hierarchy_and_bottom_up_exact_case() -> None:
    hierarchy = build_hierarchy(CATALOG)
    observations: dict[date, dict[str, float | None]] = {
        date(2026, 1, 1): {ALL_ITEMS: 100.0, FIRST: 100.0, SECOND: 100.0},
        date(2026, 2, 1): {ALL_ITEMS: 102.5, FIRST: 110.0, SECOND: 100.0},
    }
    weights = {date(2026, 2, 1): {ALL_ITEMS: 1000.0, FIRST: 250.0, SECOND: 750.0}}
    weight_results, weight_skips = validate_weight_sums(weights, hierarchy)
    bottom_results, bottom_skips = validate_bottom_up(
        observations, weights, hierarchy, tolerance_pp=1e-12
    )
    assert weight_results[0]["passed"] is True
    assert bottom_results[0]["passed"] is True
    assert bottom_results[0]["residual"] == pytest.approx(0.0, abs=1e-12)
    assert weight_skips == Counter()
    assert bottom_skips == Counter()


def test_bottom_up_flags_material_mismatch() -> None:
    observations: dict[date, dict[str, float | None]] = {
        date(2026, 1, 1): {ALL_ITEMS: 100.0, FIRST: 100.0, SECOND: 100.0},
        date(2026, 2, 1): {ALL_ITEMS: 110.0, FIRST: 100.0, SECOND: 100.0},
    }
    weights = {date(2026, 2, 1): {ALL_ITEMS: 1000.0, FIRST: 500.0, SECOND: 500.0}}
    results, _ = validate_bottom_up(
        observations, weights, build_hierarchy(CATALOG), tolerance_pp=0.1
    )
    result = results[0]
    assert result["passed"] is False
    assert result["residual"] == pytest.approx(-10.0)


def test_analytical_aggregates_stay_out_of_the_coicop_tree() -> None:
    """The ONS analytical cuts overlap, so they are not one additive tree."""
    hierarchy = build_hierarchy(CATALOG)
    assert "CPI_ALT_A02_D7F4" not in hierarchy
    assert all("CPI_ALT_A02_D7F4" not in children for children in hierarchy.values())
