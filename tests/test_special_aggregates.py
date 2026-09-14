"""Tests for the reviewed MM23 ex-CPI identity and validation layer."""

from __future__ import annotations

import csv
import io
from datetime import date

import pytest

from scripts.special_aggregates import (
    EX_CPI_SPECIAL_AGGREGATES,
    MM23_WEIGHT_TOTAL,
    complement_weight_checks,
    parse_mm23_special_aggregates,
    required_mm23_cdids,
    resolve_table38_alt_series,
    validate_crosswalk,
)


def _mm23_blob(*, omit: str | None = None, broken_total: bool = False) -> bytes:
    """Build a tiny wide MM23 file with the same title/header/period contract."""
    cdids = [cdid for cdid in sorted(required_mm23_cdids()) if cdid != omit]
    weight_cdids = {
        row["weight_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES
    } | {row["complement_weight_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES}
    index_cdids = {
        row["index_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES
    } | {row["complement_index_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES}
    rate_cdids = {
        row["rate_12m_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES
    } | {row["complement_rate_12m_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES}

    annual: dict[str, object] = {cdid: "" for cdid in cdids}
    monthly: dict[str, object] = {cdid: "" for cdid in cdids}
    for aggregate in EX_CPI_SPECIAL_AGGREGATES:
        if aggregate["weight_cdid"] in annual:
            annual[aggregate["weight_cdid"]] = 800.0
        if aggregate["complement_weight_cdid"] in annual:
            annual[aggregate["complement_weight_cdid"]] = 200.0
    if broken_total and "A9FU" in annual:
        annual["A9FU"] = 799.0
    for cdid in index_cdids:
        if cdid in monthly:
            monthly[cdid] = 123.4
    for cdid in rate_cdids:
        if cdid in monthly:
            monthly[cdid] = 2.5
    # Weight columns deliberately stay blank on monthly rows, and index/rate
    # columns stay blank on the annual row, matching the mixed-frequency source.
    assert all(annual[cdid] != "" for cdid in cdids if cdid in weight_cdids)

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["Title", *[f"Series {cdid}" for cdid in cdids]])
    writer.writerow(["CDID", *cdids])
    writer.writerow(["Source dataset ID", *["MM23"] * len(cdids)])
    writer.writerow(["Unit", *[""] * len(cdids)])
    writer.writerow(["2026", *[annual[cdid] for cdid in cdids]])
    writer.writerow(["2026 Q2", *[999.0] * len(cdids)])
    writer.writerow(["2026 JUL", *[monthly[cdid] for cdid in cdids]])
    return buffer.getvalue().encode("utf-8")


def test_crosswalk_is_reviewed_and_unique() -> None:
    validate_crosswalk()
    assert len(EX_CPI_SPECIAL_AGGREGATES) == 10
    assert {row["index_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES} == {
        "DK9V",
        "DKC5",
        "DKC6",
        "DKC7",
        "DKC8",
        "DKC9",
        "DKD2",
        "DKD3",
        "DKD4",
        "DKD5",
    }
    assert len(required_mm23_cdids()) == 60


def test_resolver_matches_alt_by_native_cdid_only() -> None:
    catalog = {
        "CPI_ALT_A01_DKC6": {"family": "ALT", "native_id": "DKC6"},
        "CPI_ALT_A02_DKC5": {"family": "ALT", "native_id": "DKC5"},
        # A COICOP row with a target CDID must not be accepted as an ALT match.
        "CPI_COICOP_C0000_DK9V": {"family": "COICOP", "native_id": "DK9V"},
    }

    resolved, missing = resolve_table38_alt_series(catalog)

    assert resolved == {
        "DKC5": "CPI_ALT_A02_DKC5",
        "DKC6": "CPI_ALT_A01_DKC6",
    }
    assert "DK9V" in missing
    assert len(missing) == 8


def test_resolver_rejects_duplicate_alt_cdid() -> None:
    catalog = {
        "CPI_ALT_A01_DKC6": {"family": "ALT", "native_id": "DKC6"},
        "CPI_ALT_A99_DKC6": {"family": "ALT", "native_id": "DKC6"},
    }

    with pytest.raises(ValueError, match="duplicate ALT CDID DKC6"):
        resolve_table38_alt_series(catalog)


def test_mm23_parser_selects_only_reviewed_annual_and_monthly_values() -> None:
    panel = parse_mm23_special_aggregates(_mm23_blob())

    assert panel.annual_weights[2026]["A9FU"] == 800.0
    assert panel.annual_weights[2026]["A9G4"] == 200.0
    assert panel.monthly_indices[date(2026, 7, 1)]["DKC6"] == 123.4
    assert panel.monthly_rates_12m[date(2026, 7, 1)]["DKO8"] == 2.5
    assert date(2026, 4, 1) not in panel.monthly_indices  # quarterly row ignored


def test_mm23_parser_fails_when_a_reviewed_cdid_disappears() -> None:
    with pytest.raises(ValueError, match="missing reviewed ex-CPI CDIDs.*A9FU"):
        parse_mm23_special_aggregates(_mm23_blob(omit="A9FU"))


def test_complement_weight_checks_reconcile_to_one_thousand() -> None:
    panel = parse_mm23_special_aggregates(_mm23_blob())
    checks = complement_weight_checks(panel, latest_only=True)

    assert len(checks) == 10
    assert all(check["passed"] is True for check in checks)
    assert all(check["total"] == MM23_WEIGHT_TOTAL for check in checks)


def test_complement_weight_check_surfaces_source_inconsistency() -> None:
    panel = parse_mm23_special_aggregates(_mm23_blob(broken_total=True))
    checks = complement_weight_checks(panel, latest_only=True)
    core = next(
        check
        for check in checks
        if check["label"] == "CPI excluding energy, food, alcohol and tobacco"
    )

    assert core["passed"] is False
    assert core["residual"] == -1.0
