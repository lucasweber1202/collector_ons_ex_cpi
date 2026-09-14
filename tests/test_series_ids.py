"""Contract tests for standalone EX-CPI identity and scope."""

from __future__ import annotations

import pytest

from scripts.extract import TARGET_CDIDS, make_series_id, parse_series_id
from scripts.special_aggregates import EX_CPI_SPECIAL_AGGREGATES, required_mm23_cdids


def test_exactly_ten_required_indices() -> None:
    assert TARGET_CDIDS == {row["index_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES}
    assert len(TARGET_CDIDS) == 10


def test_crosswalk_contains_sixty_unique_role_cdids() -> None:
    assert len(required_mm23_cdids()) == 60


@pytest.mark.parametrize("native_id", sorted(TARGET_CDIDS))
def test_series_id_round_trip(native_id: str) -> None:
    series_id = make_series_id(native_id)
    assert series_id == f"EXCPI_INDEX_NATIVE_{native_id}"
    assert parse_series_id(series_id) == ("EXCPI", "INDEX", "NATIVE", native_id)


def test_human_title_cannot_change_identity() -> None:
    assert make_series_id("dkc6") == "EXCPI_INDEX_NATIVE_DKC6"


@pytest.mark.parametrize(
    "series_id",
    ["CPI_ALT_A01_DKC6", "EXCPI_DKC6", "EXCPI_INDEX_NATIVE_UNKNOWN"],
)
def test_rejects_non_contract_identifier(series_id: str) -> None:
    with pytest.raises(ValueError):
        parse_series_id(series_id)
