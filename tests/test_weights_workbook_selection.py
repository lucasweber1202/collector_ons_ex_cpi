"""The W1 weights edition must be chosen by published year, not by link order."""

from __future__ import annotations

import pytest

from scripts.extract import _latest_weights_url

_BASE = (
    "/file?uri=/economy/inflationandpriceindices/datasets/"
    "consumerpriceinflationupdatingweightsannexatablesw1tow3/"
)


def _link(year: int) -> str:
    return (
        f'<a href="{_BASE}annexatablesw1tow3weights{year}/annexaw1w3weights{year}.xlsx">{year}</a>'
    )


def test_newest_year_wins_when_listed_first() -> None:
    page = "".join(_link(year) for year in (2026, 2025, 2024, 2023))
    assert _latest_weights_url(page).endswith("annexaw1w3weights2026.xlsx")


def test_newest_year_wins_when_listed_last() -> None:
    """ONS orders the page today; nothing in the contract promises it tomorrow."""
    page = "".join(_link(year) for year in (2023, 2024, 2025, 2026))
    assert _latest_weights_url(page).endswith("annexaw1w3weights2026.xlsx")


def test_newest_year_wins_in_arbitrary_order() -> None:
    page = "".join(_link(year) for year in (2024, 2026, 2023, 2025))
    assert _latest_weights_url(page).endswith("annexaw1w3weights2026.xlsx")


def test_single_edition_is_accepted() -> None:
    assert _latest_weights_url(_link(2019)).endswith("annexaw1w3weights2019.xlsx")


def test_missing_workbook_fails_loudly() -> None:
    """Silently ingesting a stale basket is worse than stopping the run."""
    with pytest.raises(ValueError, match="annexaw1w3weights"):
        _latest_weights_url('<a href="/file?uri=/some/other/table.xlsx">other</a>')


def test_unexpected_rename_is_not_guessed_at() -> None:
    """An unrelated XLSX on the page must never become the basket by default."""
    page = '<a href="/file?uri=/economy/consumerprices/basketofgoods2026.xlsx">renamed</a>'
    with pytest.raises(ValueError, match="publication layout changed"):
        _latest_weights_url(page)


def test_other_xlsx_on_the_page_is_ignored() -> None:
    page = _link(2025) + '<a href="/file?uri=/economy/otherweights2030.xlsx">noise</a>'
    assert _latest_weights_url(page).endswith("annexaw1w3weights2025.xlsx")
