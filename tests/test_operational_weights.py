"""Operational weights must reproduce rates without another price update."""

from __future__ import annotations

from datetime import date

import pytest

from scripts import validate
from tests.test_validation_coverage import FIRST, HIERARCHY, OBSERVATIONS, PARENT, SECOND


def test_operational_weights_are_local_shares_with_unit_root() -> None:
    feb = date(2026, 2, 1)
    original = {feb: {PARENT: 1000.0, FIRST: 250.0, SECOND: 750.0}}
    result = validate.derive_operational_weights(OBSERVATIONS, original, HIERARCHY)
    assert result[feb] == {PARENT: 1.0, FIRST: 0.25, SECOND: 0.75}
    checks, skips = validate.validate_bottom_up(OBSERVATIONS, result, HIERARCHY, operational=True)
    assert not skips
    assert checks[0]["residual"] == pytest.approx(0)


def test_missing_price_reference_cannot_produce_weights() -> None:
    feb = date(2026, 2, 1)
    assert (
        validate.derive_operational_weights(
            {feb: OBSERVATIONS[feb]}, {feb: {FIRST: 250, SECOND: 750}}, HIERARCHY
        )
        == {}
    )


def test_operational_validation_does_not_renormalize_bad_weights() -> None:
    feb = date(2026, 2, 1)
    bad = {feb: {PARENT: 1.0, FIRST: 0.125, SECOND: 0.375}}
    checks, _ = validate.validate_bottom_up(OBSERVATIONS, bad, HIERARCHY, operational=True)
    assert checks[0]["passed"] is False


def test_standalone_analytical_aggregate_gets_unit_weight() -> None:
    feb = date(2026, 2, 1)
    observations = {
        month: {**values, "CPI_ALT_A02_D7F4": 100.0} for month, values in OBSERVATIONS.items()
    }
    result = validate.derive_operational_weights(
        observations, {feb: {PARENT: 1000, FIRST: 250, SECOND: 750}}, HIERARCHY
    )
    assert result[feb]["CPI_ALT_A02_D7F4"] == 1.0
