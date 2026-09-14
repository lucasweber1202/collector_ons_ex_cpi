"""Tests for Table 38 -> MM23 published 12-month-rate reconciliation."""

from __future__ import annotations

from datetime import date

import pytest

from scripts.special_aggregate_rates import published_12m_rate_checks
from scripts.special_aggregates import EX_CPI_SPECIAL_AGGREGATES, MM23SpecialPanel


def _rate_fixture() -> tuple[
    dict[date, dict[str, float | None]],
    dict[str, dict[str, str]],
    MM23SpecialPanel,
]:
    previous_month = date(2025, 7, 1)
    current_month = date(2026, 7, 1)
    catalog: dict[str, dict[str, str]] = {}
    previous: dict[str, float | None] = {}
    current: dict[str, float | None] = {}
    rates: dict[str, float] = {}
    for index, aggregate in enumerate(EX_CPI_SPECIAL_AGGREGATES, start=1):
        series_id = f"CPI_ALT_A{index:02d}_{aggregate['index_cdid']}"
        catalog[series_id] = {
            "family": "ALT",
            "native_id": aggregate["index_cdid"],
        }
        previous[series_id] = 100.0
        current[series_id] = 102.5
        rates[aggregate["rate_12m_cdid"]] = 2.5
    observations = {
        previous_month: previous,
        current_month: current,
    }
    panel = MM23SpecialPanel(
        annual_weights={},
        monthly_indices={},
        monthly_rates_12m={current_month: rates},
    )
    return observations, catalog, panel


def test_published_12m_rates_reconcile_for_all_reviewed_exclusions() -> None:
    observations, catalog, panel = _rate_fixture()

    checks = published_12m_rate_checks(observations, catalog, panel, latest_only=True)

    assert len(checks) == len(EX_CPI_SPECIAL_AGGREGATES)
    assert all(check["passed"] is True for check in checks)
    assert all(check["residual_pp"] == pytest.approx(0.0) for check in checks)


def test_published_12m_rate_check_surfaces_a_wrong_mm23_rate() -> None:
    observations, catalog, panel = _rate_fixture()
    current_month = date(2026, 7, 1)
    panel.monthly_rates_12m[current_month]["DKO8"] = 2.3

    checks = published_12m_rate_checks(observations, catalog, panel, latest_only=True)
    core = next(
        check
        for check in checks
        if check["label"] == "CPI excluding energy, food, alcohol and tobacco"
    )

    assert core["passed"] is False
    assert core["residual_pp"] == pytest.approx(0.2)


def test_published_12m_rate_check_rejects_missing_table38_alt_target() -> None:
    observations, catalog, panel = _rate_fixture()
    missing_series = next(
        series_id for series_id, fields in catalog.items() if fields["native_id"] == "DKC6"
    )
    del catalog[missing_series]

    with pytest.raises(ValueError, match="Table 38 ALT CDIDs missing.*DKC6"):
        published_12m_rate_checks(observations, catalog, panel)


def test_published_12m_rate_check_can_keep_only_latest_common_month() -> None:
    observations, catalog, panel = _rate_fixture()
    older_previous = date(2025, 6, 1)
    older_month = date(2026, 6, 1)
    observations[older_previous] = dict(observations[date(2025, 7, 1)])
    observations[older_month] = dict(observations[date(2026, 7, 1)])
    panel.monthly_rates_12m[older_month] = dict(panel.monthly_rates_12m[date(2026, 7, 1)])

    all_checks = published_12m_rate_checks(observations, catalog, panel)
    latest = published_12m_rate_checks(observations, catalog, panel, latest_only=True)

    assert len(all_checks) == 2 * len(EX_CPI_SPECIAL_AGGREGATES)
    assert len(latest) == len(EX_CPI_SPECIAL_AGGREGATES)
    assert {check["month"] for check in latest} == {date(2026, 7, 1)}
