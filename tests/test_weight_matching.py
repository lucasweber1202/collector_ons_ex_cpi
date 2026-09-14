"""Regression tests for W1 weight attribution: duplicates, conflicts, ambiguity."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date

import pytest

from scripts.extract import (
    get_original_weight_catalog,
    get_original_weights,
    parse_weights_workbook,
)
from tests.conftest import catalog_entry

_EDUCATION = catalog_entry("COICOP", "D10", "D7C5", "EDUCATION", classification="10")
_HEALTH = catalog_entry("COICOP", "D06", "D7BX", "HEALTH", classification="6")
EDUCATION, HEALTH = _EDUCATION[0], _HEALTH[0]
CATALOG = dict([_EDUCATION, _HEALTH])
JUNE = date(2026, 6, 1)

Workbook = Callable[..., bytes]


def test_agreeing_duplicate_rows_are_reported_but_keep_the_value(
    weights_workbook: Workbook, caplog: pytest.LogCaptureFixture
) -> None:
    """Two rows for one series must be surfaced even when the values agree."""
    blob = weights_workbook([("10 Education", 34.2466), ("10.0 Education", 34.2466)])

    with caplog.at_level(logging.WARNING, logger="scripts.extract"):
        parsed = parse_weights_workbook(blob, CATALOG)

    assert parsed[JUNE][EDUCATION] == pytest.approx(34.2466)
    assert any("both map to" in record.getMessage() for record in caplog.records)
    assert not any("Conflicting W1 weights" in record.getMessage() for record in caplog.records)


def test_conflicting_duplicate_rows_are_reported(weights_workbook: Workbook) -> None:
    """Disagreeing duplicates make the stored weight depend on row order."""
    blob = weights_workbook([("10 Education", 34.2466), ("10.0 Education", 99.9999)])

    with pytest.raises(ValueError, match="Conflicting W1 weights"):
        parse_weights_workbook(blob, CATALOG)


def test_unmatched_row_is_reported_and_kept_only_as_an_official_weight(
    weights_workbook: Workbook, caplog: pytest.LogCaptureFixture
) -> None:
    """A W1 row with no Table 38 counterpart never becomes an invented series."""
    blob = weights_workbook([("10 Education", 34.2466), ("10.4 Tertiary education", 5.0)])

    with caplog.at_level(logging.WARNING, logger="scripts.extract"):
        parsed = parse_weights_workbook(blob, CATALOG)

    assert set(parsed[JUNE]) == {EDUCATION}
    assert get_original_weights()[JUNE]["CPI_W1_10P4"] == 5.0
    assert any("matched no Table 38 series" in record.getMessage() for record in caplog.records)


def test_start_date_still_filters_expanded_months(weights_workbook: Workbook) -> None:
    """The regime expansion keeps honouring the extraction window."""
    blob = weights_workbook([("10 Education", 34.2466)])

    parsed = parse_weights_workbook(blob, CATALOG, start_date=date(2026, 7, 1))

    assert min(parsed) == date(2026, 7, 1)
    assert max(parsed) == date(2026, 12, 1)


def test_ambiguous_node_is_rejected(weights_workbook: Workbook) -> None:
    catalog = dict(
        entry
        for entry in (
            catalog_entry("COICOP", "D10", "ONE", "Education", classification="10"),
            catalog_entry("COICOP", "D10", "TWO", "Education", classification="10"),
        )
    )
    with pytest.raises(ValueError, match="Ambiguous"):
        parse_weights_workbook(weights_workbook([("10 Education", 34.0)]), catalog)


def test_reviewed_alias_without_its_cdid_is_rejected(weights_workbook: Workbook) -> None:
    """A merged class must never silently fall back to a node match."""
    with pytest.raises(ValueError, match="requires missing CDID"):
        parse_weights_workbook(
            weights_workbook([("07.3.2/6 Passenger transport by road", 10.0)]), CATALOG
        )


def test_subclass_weights_are_preserved_without_fake_index_metadata(
    weights_workbook: Workbook,
) -> None:
    """A weight-only subclass keeps its official weight and gains no series."""
    blob = weights_workbook([("10 Education", 34.0), ("01.1.1.1 Rice", 2.0)])

    parsed = parse_weights_workbook(blob, CATALOG)

    assert set(parsed[JUNE]) == {EDUCATION}
    original = get_original_weights()[JUNE]
    assert original["CPI_W1_01P1P1P1"] == 2.0
    assert original["CPI_W1_10"] == 34.0
    catalog = get_original_weight_catalog()
    assert catalog["CPI_W1_01P1P1P1"]["mapped_series_id"] == ""
    assert catalog["CPI_W1_10"]["mapped_series_id"] == EDUCATION
    assert catalog["CPI_W1_10"]["native_id"] == "TEST"


def test_negative_published_weight_is_rejected(weights_workbook: Workbook) -> None:
    with pytest.raises(ValueError, match="Negative W1 weight"):
        parse_weights_workbook(weights_workbook([("10 Education", -1.0)]), CATALOG)
