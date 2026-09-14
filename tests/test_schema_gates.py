"""Layout drift must stop the run before anything reaches the database."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest

from scripts import extract, segments
from tests.conftest import default_table38_series


def test_valid_layout_parses(cpi_workbook: Callable[..., bytes]) -> None:
    parsed = extract.parse_cpi_workbook(cpi_workbook())
    assert len(parsed) == 130
    assert extract.get_last_publish_date() == date(2026, 8, 19)
    catalog = extract.get_series_catalog()
    assert catalog["CPI_COICOP_D01_D7BU"]["parent_series_id"] == "CPI_COICOP_ALL_D7BT"
    assert catalog["CPI_COICOP_C0111_D7D5"]["parent_series_id"] == "CPI_COICOP_G011_D7C8"
    assert catalog["CPI_ALT_A02_D7F4"]["parent_series_id"] == ""


def test_missing_sheet_is_named(cpi_workbook: Callable[..., bytes]) -> None:
    with pytest.raises(ValueError, match="no sheet 'Table 38'"):
        extract.parse_cpi_workbook(cpi_workbook(sheet_name="Table 39"))


def test_moved_header_row_is_rejected(cpi_workbook: Callable[..., bytes]) -> None:
    """A shifted header means the trusted column positions no longer hold."""
    with pytest.raises(ValueError, match="should label 'aggregate number'"):
        extract.parse_cpi_workbook(cpi_workbook(code_label="classification"))


def test_lost_anchor_series_is_rejected(cpi_workbook: Callable[..., bytes]) -> None:
    series = [row for row in default_table38_series() if row[1] != "D7BT"]
    with pytest.raises(ValueError, match=r"required CDIDs: \['D7BT'\]"):
        extract.parse_cpi_workbook(cpi_workbook(series))


def test_collapsed_series_count_is_rejected(cpi_workbook: Callable[..., bytes]) -> None:
    """A basket revision may move series; losing nearly all of them cannot."""
    series = default_table38_series()[:8]
    with pytest.raises(ValueError, match="collapsed to 8 series"):
        extract.parse_cpi_workbook(cpi_workbook(series))


def test_truncated_history_is_rejected(cpi_workbook: Callable[..., bytes]) -> None:
    with pytest.raises(ValueError, match="below the 120 floor"):
        extract.parse_cpi_workbook(cpi_workbook(months=24))


def test_duplicate_column_is_rejected(cpi_workbook: Callable[..., bytes]) -> None:
    series = [*default_table38_series(), ("0", "D7BT", "CPI ALL ITEMS")]
    with pytest.raises(ValueError, match="two columns"):
        extract.parse_cpi_workbook(cpi_workbook(series))


def test_weights_sheet_must_carry_enough_regimes(
    cpi_workbook: Callable[..., bytes], weights_workbook: Callable[..., bytes]
) -> None:
    extract.parse_cpi_workbook(cpi_workbook())
    blob = weights_workbook([("01 Food and non-alcoholic beverages", 100.0)], regimes=3)
    with pytest.raises(ValueError, match="weight regimes, below"):
        extract.parse_weights_workbook(blob, extract.get_series_catalog())


def test_weights_sheet_must_keep_the_published_total(
    cpi_workbook: Callable[..., bytes], weights_workbook: Callable[..., bytes]
) -> None:
    extract.parse_cpi_workbook(cpi_workbook())
    blob = weights_workbook([("01 Food and non-alcoholic beverages", 100.0)])
    parsed = extract.parse_weights_workbook(blob, extract.get_series_catalog())
    assert parsed[date(2026, 6, 1)]["CPI_COICOP_D01_D7BU"] == 100.0
    assert extract.get_original_weights()[date(2026, 6, 1)]["CPI_W1_0"] == 1000.0


SEGMENT_HEADER = "INDEX_DATE,COICOP4_ID,COICOP5_ID,RPI_SECTION,CS_ID,CS_DESC,RPI_INDEX,CPI_INDEX,CPI_WEIGHT,RPI_WEIGHT,CPIH_WEIGHT"  # noqa: E501


def _segment_csv(rows: int = 400, *, stamp: str = "202607", weight: float = 2.5) -> bytes:
    body = [SEGMENT_HEADER]
    for index in range(rows):
        body.append(
            f"{stamp},CP0111,CP01113,RP2101,CP011{index:04d},SEGMENT {index},"
            f"100.0,100.{index % 10},{weight},1.0,1.0"
        )
    return "\n".join(body).encode()


def test_segment_file_parses_and_gates_weight_total() -> None:
    frame = segments.parse_segment_csv(_segment_csv(), date(2026, 7, 1))
    assert len(frame) == 400
    assert frame["CPI_WEIGHT"].sum() == pytest.approx(1000.0)


def test_segment_rate_limit_body_is_rejected() -> None:
    """ONS answers a burst with HTTP 200 and a plain-text notice, not the file."""
    with pytest.raises(ValueError, match="missing columns"):
        segments.parse_segment_csv(b"error code: 1015\n", date(2026, 7, 1))


def test_segment_file_for_the_wrong_month_is_rejected() -> None:
    with pytest.raises(ValueError, match="carries INDEX_DATE"):
        segments.parse_segment_csv(_segment_csv(stamp="202606"), date(2026, 7, 1))


def test_collapsed_segment_file_is_rejected() -> None:
    with pytest.raises(ValueError, match="below the 300 floor"):
        segments.parse_segment_csv(_segment_csv(rows=10), date(2026, 7, 1))


def test_segment_weight_total_outside_the_published_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="parts per thousand"):
        segments.parse_segment_csv(_segment_csv(weight=0.5), date(2026, 7, 1))
