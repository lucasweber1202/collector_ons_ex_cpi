"""Opt-in checks against the current official ONS files."""

from __future__ import annotations

import os
from datetime import date

import pytest

from scripts.extract import TARGET_CDIDS, collect_raw_data, get_series_catalog
from scripts.special_aggregate_rates import published_12m_rate_checks
from scripts.special_aggregates import (
    EX_CPI_SPECIAL_AGGREGATES,
    collect_mm23_special_aggregates,
    complement_weight_checks,
)


@pytest.mark.skipif(os.getenv("ONS_LIVE_TEST") != "1", reason="explicit live-source opt-in")
def test_live_ons_ex_cpi_scope_and_reconciliation() -> None:
    observations = collect_raw_data(date(2000, 1, 1))
    catalog = get_series_catalog()
    assert len(catalog) == 10
    assert {fields["native_id"] for fields in catalog.values()} == TARGET_CDIDS
    assert all(len(values) == 10 for values in observations.values())

    mm23 = collect_mm23_special_aggregates()
    weight_checks = complement_weight_checks(mm23, latest_only=True)
    rate_checks = published_12m_rate_checks(
        observations, catalog, mm23, latest_only=True
    )
    assert len(weight_checks) == len(EX_CPI_SPECIAL_AGGREGATES)
    assert len(rate_checks) == len(EX_CPI_SPECIAL_AGGREGATES)
    assert all(check["passed"] for check in weight_checks)
    assert all(check["passed"] for check in rate_checks)
