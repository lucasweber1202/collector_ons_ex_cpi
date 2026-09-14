"""Tests for MM23 snapshot discovery and source-backed weight regimes."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from scripts.special_aggregate_vintages import (
    build_exclusion_weight_regimes,
    build_mm23_original_weight_layer,
    january_regime_snapshots,
    map_exclusion_weight_regimes_to_table38,
    mm23_original_weight_id,
    parse_mm23_snapshot_index,
)
from scripts.special_aggregates import EX_CPI_SPECIAL_AGGREGATES, MM23SpecialPanel


def _row(version: str, reason: str, superseded: str) -> str:
    href = (
        "/file?uri=%2Feconomy%2Finflationandpriceindices%2Fdatasets%2F"
        "consumerpriceindices%2Fcurrent%2Fprevious%2F"
        f"{version}%2Fmm23.csv"
    )
    return (
        "<tr>"
        f'<td><a href="{href}">csv</a></td>'
        f"<td>{reason}</td>"
        f"<td>{superseded}</td>"
        "</tr>"
    )


def _page(*rows: str) -> str:
    return "<table>" + "".join(rows) + "</table>"


def _weight_panel(values_by_year: dict[int, float]) -> MM23SpecialPanel:
    annual: dict[int, dict[str, float]] = {}
    for year, value in values_by_year.items():
        annual[year] = {
            aggregate["weight_cdid"]: value for aggregate in EX_CPI_SPECIAL_AGGREGATES
        }
    return MM23SpecialPanel(annual_weights=annual, monthly_indices={}, monthly_rates_12m={})


def _alt_catalog() -> dict[str, dict[str, str]]:
    return {
        f"EXCPI_INDEX_A{index:02d}_{aggregate['index_cdid']}": {
            "family": "INDEX",
            "native_id": aggregate["index_cdid"],
        }
        for index, aggregate in enumerate(EX_CPI_SPECIAL_AGGREGATES, start=1)
    }


def test_snapshot_index_parses_version_url_date_and_reason() -> None:
    page = _page(_row("v130", "Scheduled update/revision", "25 March 2026 07:00"))

    snapshots = parse_mm23_snapshot_index(page)

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.version_id == "v130"
    assert snapshot.superseded_at == datetime(2026, 3, 25, 7, 0, tzinfo=UTC)
    assert snapshot.reason == "scheduled"
    assert "previous%2Fv130%2Fmm23.csv" in snapshot.csv_url


def test_january_regime_uses_scheduled_march_snapshot_not_same_day_correction() -> None:
    page = _page(
        _row("v118", "Scheduled update/revision", "26 March 2025 07:00"),
        _row("v130", "Scheduled update/revision", "25 March 2026 07:00"),
        _row("v131", "Correction See correction", "25 March 2026 12:51"),
    )

    selected = january_regime_snapshots(parse_mm23_snapshot_index(page))

    assert selected[2025].version_id == "v118"
    assert selected[2026].version_id == "v130"
    assert all(snapshot.reason == "scheduled" for snapshot in selected.values())


def test_january_regime_ignores_pre_double_update_years() -> None:
    page = _page(
        _row("v1", "Scheduled update/revision", "23 March 2016 07:00"),
        _row("v2", "Scheduled update/revision", "21 March 2017 07:00"),
    )

    selected = january_regime_snapshots(parse_mm23_snapshot_index(page))

    assert set(selected) == {2017}


def test_january_regime_rejects_two_scheduled_march_snapshots_for_one_year() -> None:
    page = _page(
        _row("v130", "Scheduled update/revision", "25 March 2026 07:00"),
        _row("v131", "Scheduled update/revision", "25 March 2026 12:51"),
    )

    with pytest.raises(ValueError, match="Two scheduled March MM23 snapshots found for 2026"):
        january_regime_snapshots(parse_mm23_snapshot_index(page))


def test_snapshot_index_fails_loudly_when_layout_has_no_versioned_csv() -> None:
    page = _page("<tr><td>No archived files</td><td>25 March 2026 07:00</td></tr>")

    with pytest.raises(ValueError, match="no versioned CSV snapshots"):
        parse_mm23_snapshot_index(page)


def test_weight_regimes_use_archived_january_and_current_february_to_december() -> None:
    current = _weight_panel({2026: 794.8781})
    january = _weight_panel({2026: 796.0030})

    expanded = build_exclusion_weight_regimes(
        current,
        date(2026, 8, 19),
        {2026: january},
    )

    assert expanded[date(2026, 1, 1)]["A9FU"] == 796.0030
    assert expanded[date(2026, 2, 1)]["A9FU"] == 794.8781
    assert expanded[date(2026, 12, 1)]["A9FU"] == 794.8781
    assert len(expanded) == 12


def test_february_release_uses_current_value_for_january_only() -> None:
    current = _weight_panel({2026: 796.0030})

    expanded = build_exclusion_weight_regimes(
        current,
        date(2026, 2, 18),
        {},
    )

    assert set(expanded) == {date(2026, 1, 1)}
    assert expanded[date(2026, 1, 1)]["A9FU"] == 796.0030


def test_historical_double_update_refuses_to_fabricate_missing_january() -> None:
    current = _weight_panel({2025: 787.1987, 2026: 794.8781})

    with pytest.raises(ValueError, match="Missing archived January MM23 weight panel for 2025"):
        build_exclusion_weight_regimes(
            current,
            date(2026, 8, 19),
            {2026: _weight_panel({2026: 796.0030})},
            start_year=2025,
        )


def test_pre_2017_weight_applies_to_all_twelve_months_without_archive() -> None:
    current = _weight_panel({2016: 788.0})

    expanded = build_exclusion_weight_regimes(
        current,
        date(2026, 8, 19),
        {},
    )

    assert len(expanded) == 12
    assert {month.month for month in expanded} == set(range(1, 13))
    assert all(values["A9FU"] == 788.0 for values in expanded.values())


def test_weight_regimes_map_onto_existing_alt_series_ids() -> None:
    regimes = {
        date(2026, 1, 1): {
            aggregate["weight_cdid"]: 700.0 + index
            for index, aggregate in enumerate(EX_CPI_SPECIAL_AGGREGATES)
        }
    }

    mapped = map_exclusion_weight_regimes_to_table38(regimes, _alt_catalog())

    core_series = next(
        series_id for series_id in _alt_catalog() if series_id.endswith("_DKC6")
    )
    assert mapped[date(2026, 1, 1)][core_series] == 702.0
    assert len(mapped[date(2026, 1, 1)]) == len(EX_CPI_SPECIAL_AGGREGATES)


def test_weight_regime_mapping_rejects_missing_alt_target() -> None:
    regimes = {
        date(2026, 1, 1): {
            aggregate["weight_cdid"]: 800.0 for aggregate in EX_CPI_SPECIAL_AGGREGATES
        }
    }
    catalog = _alt_catalog()
    missing = next(series_id for series_id in catalog if series_id.endswith("_DKC6"))
    del catalog[missing]

    with pytest.raises(ValueError, match="Table 38 ALT CDIDs missing.*DKC6"):
        map_exclusion_weight_regimes_to_table38(regimes, catalog)


def test_mm23_original_weight_layer_preserves_source_cdid_and_alt_mapping() -> None:
    regimes = {
        date(2026, 1, 1): {
            aggregate["weight_cdid"]: 700.0 + index
            for index, aggregate in enumerate(EX_CPI_SPECIAL_AGGREGATES)
        }
    }

    originals, audit = build_mm23_original_weight_layer(regimes, _alt_catalog())

    assert originals[date(2026, 1, 1)]["EXCPI_WEIGHT_NATIVE_A9FU"] == 702.0
    assert audit["EXCPI_WEIGHT_NATIVE_A9FU"]["native_id"] == "A9FU"
    assert audit["EXCPI_WEIGHT_NATIVE_A9FU"]["mapped_series_id"].endswith("_DKC6")
    assert audit["EXCPI_WEIGHT_NATIVE_A9FU"]["dataset"].startswith("ONS Consumer price inflation")
    assert len(originals[date(2026, 1, 1)]) == 2 * len(EX_CPI_SPECIAL_AGGREGATES)
    assert len(audit) == 2 * len(EX_CPI_SPECIAL_AGGREGATES)


def test_mm23_original_weight_id_normalizes_native_cdid() -> None:
    assert mm23_original_weight_id(" a9fu ") == "EXCPI_WEIGHT_NATIVE_A9FU"
    with pytest.raises(ValueError, match="CDID is empty"):
        mm23_original_weight_id("---")
