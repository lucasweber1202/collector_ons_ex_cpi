"""Workbook cells arrive in many spellings; each must resolve to one decision."""

from __future__ import annotations

import math
from datetime import date, datetime

import pandas as pd
import pytest

from scripts.extract import cell_float, cell_month, cell_text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (142.933, 142.933),
        (0, 0.0),
        (-1.5, -1.5),
        ("142.933", 142.933),
        ("  142.933 ", 142.933),
        (pd.NA, None),
        (pd.NaT, None),
        (None, None),
        (float("nan"), None),
        (float("inf"), None),
        (float("-inf"), None),
        ("..", None),  # the ONS not-available marker
        ("", None),
        ("   ", None),
        ("n/a", None),
        (date(2026, 7, 1), None),
    ],
)
def test_cell_float(value: object, expected: float | None) -> None:
    assert cell_float(value) == expected


def test_numpy_scalars_are_numeric() -> None:
    """pandas hands back numpy scalars, not Python floats."""
    frame = pd.DataFrame([[142.933, 5]])
    assert cell_float(frame.iat[0, 0]) == 142.933
    assert cell_float(frame.iat[0, 1]) == 5.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("D7BU", "D7BU"),
        ("  01.1.1  ", "01.1.1"),
        (1, "1"),
        (1.0, "1.0"),
        (pd.NA, None),
        (pd.NaT, None),
        (None, None),
        (float("nan"), None),
        ("", None),
        ("   ", None),
    ],
)
def test_cell_text(value: object, expected: str | None) -> None:
    assert cell_text(value) == expected


def test_missing_markers_never_become_labels() -> None:
    """str(pd.NaT) is "NaT", which would read as a perfectly good series name."""
    for marker in (pd.NaT, pd.NA):
        assert cell_text(marker) is None
        assert str(marker) not in ("", "None")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime(2026, 7, 14), date(2026, 7, 1)),
        (date(2026, 7, 14), date(2026, 7, 1)),
        ("2026-07-14", date(2026, 7, 1)),
        (pd.Timestamp("2026-07-14"), date(2026, 7, 1)),
        ("Key: .. not available", None),
        (142.933, None),
        (None, None),
        (float("nan"), None),
    ],
)
def test_cell_month(value: object, expected: date | None) -> None:
    assert cell_month(value) == expected


def test_footer_text_below_the_data_is_not_a_month() -> None:
    """Table 38 carries a legend under its last row; it must not parse as a date."""
    assert cell_month("Key:    - zero or negligible ..  not available") is None


def test_a_finite_value_survives_the_round_trip() -> None:
    for value in (48.395, 142.933, 1e-9, 1e9):
        parsed = cell_float(value)
        assert parsed is not None
        assert math.isfinite(parsed)
        assert parsed == value
