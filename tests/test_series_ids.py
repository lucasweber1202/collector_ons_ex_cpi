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


def test_metadata_country_is_the_iso_4217_currency_code() -> None:
    """The fleet vocabulary for ``metadata.country`` is currency, not country.

    This collector emitted ``GBR``, the ISO 3166 code, while the rest of the
    fleet uses ISO 4217. ``MASTER_MACRO_COLLECTOR_GUIDELINES.md`` section 5.1
    pins the vocabulary, so a regression here is a fleet-contract break rather
    than a cosmetic one.
    """
    from scripts.extract import COUNTRY_CURRENCY

    assert COUNTRY_CURRENCY == "GBP"
    assert COUNTRY_CURRENCY != "GBR"


def test_every_metadata_row_carries_gbp() -> None:
    """The constant must actually reach the stored column."""
    from datetime import UTC, date, datetime

    from scripts.extract import SOURCE_URL, TABLE38_DATASET, TABLE38_PROVENANCE
    from scripts.metadata import build_metadata_rows

    series_id = "EXCPI_INDEX_NATIVE_DKC6"
    catalog = {
        series_id: {
            "family": "INDEX",
            "node": "NATIVE",
            "level": "special_aggregate",
            "native_id": "DKC6",
            "name": "CPI excluding energy, food, alcohol and tobacco",
            "classification": "",
            "dataset": TABLE38_DATASET,
            "source_url": SOURCE_URL,
            "provenance": TABLE38_PROVENANCE,
            "parent_series_id": "",
        }
    }
    aggregates = {
        series_id: {
            "first_observation": date(1988, 1, 1),
            "last_observation": date(2026, 7, 1),
            "observation_count": 463,
            "last_collected_at": datetime(2026, 9, 14, tzinfo=UTC),
        }
    }
    rows = build_metadata_rows(
        {date(2026, 7, 1): {series_id: 100.0}},
        aggregates,
        datetime(2026, 9, 14, tzinfo=UTC),
        catalog,
    )

    assert rows, "expected one metadata row"
    assert {row["country"] for row in rows} == {"GBP"}
