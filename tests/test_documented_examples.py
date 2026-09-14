"""The worked examples in METHODOLOGY.md must stay true to the code.

Each case feeds the documented inputs through the shipped functions and asserts
the documented outputs, so a formula change cannot leave the documentation
quietly wrong.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from scripts.config import ROOT_DIR
from scripts.validate import (
    build_hierarchy,
    derive_operational_weights,
    derive_segment_operational_weights,
    validate_bottom_up,
    validate_segment_bottom_up,
)
from tests.conftest import catalog_entry

JANUARY, JUNE, JULY = date(2026, 1, 1), date(2026, 6, 1), date(2026, 7, 1)

_DIVISION = catalog_entry("COICOP", "D01", "D7BU", "FOOD AND NON-ALCOHOLIC BEVERAGES")
_FOOD = catalog_entry("COICOP", "G011", "D7C8", "FOOD", level="group", parent=_DIVISION[0])
_DRINK = catalog_entry(
    "COICOP", "G012", "D7C9", "NON-ALCOHOLIC BEVERAGES", level="group", parent=_DIVISION[0]
)
DIVISION, FOOD, DRINK = _DIVISION[0], _FOOD[0], _DRINK[0]
COICOP_CATALOG = dict([_DIVISION, _FOOD, _DRINK])

# Published ONS levels for the documented months.
COICOP_LEVELS: dict[date, dict[str, float | None]] = {
    JANUARY: {DIVISION: 143.372, FOOD: 143.254, DRINK: 149.471},
    JUNE: {DIVISION: 144.032, FOOD: 143.166, DRINK: 150.981},
    JULY: {DIVISION: 144.029, FOOD: 143.151, DRINK: 151.078},
}
W1_BASKET = {
    JUNE: {DIVISION: 109.6059, FOOD: 97.2974, DRINK: 12.3085},
    JULY: {DIVISION: 109.6059, FOOD: 97.2974, DRINK: 12.3085},
}
DOCUMENTED_PHI_FOOD = 0.8866345377
DOCUMENTED_COICOP_RESIDUAL_PP = 0.0000766255

GAS = "CPI_COICOP_C0452_D7DU"
SEGMENTS = ("CPI_CS_SEG_420301", "CPI_CS_SEG_420302", "CPI_CS_SEG_420404")
SEGMENT_LEVELS: dict[date, dict[str, float | None]] = {
    JUNE: {GAS: 138.611, SEGMENTS[0]: 94.615, SEGMENTS[1]: 100.019, SEGMENTS[2]: 100.951},
    JULY: {GAS: 158.997, SEGMENTS[0]: 116.254, SEGMENTS[1]: 101.473, SEGMENTS[2]: 104.392},
}
SEGMENT_WEIGHTS = {
    JUNE: dict(zip(SEGMENTS, (6.479, 3.489, 0.333), strict=True)),
    JULY: dict(zip(SEGMENTS, (6.479, 3.489, 0.333), strict=True)),
}
SEGMENT_HIERARCHY = {JUNE: {GAS: list(SEGMENTS)}, JULY: {GAS: list(SEGMENTS)}}
DOCUMENTED_SEGMENT_PHI = (0.6157237353, 0.3505107961, 0.0337654685)
DOCUMENTED_SEGMENT_RESIDUAL_PP = -0.0007487


def _methodology() -> str:
    return (Path(ROOT_DIR) / "METHODOLOGY.md").read_text(encoding="utf-8")


def test_documented_coicop_shares_and_residual() -> None:
    """01 Food and non-alcoholic beverages from its two published groups."""
    hierarchy = build_hierarchy(COICOP_CATALOG)
    operational = derive_operational_weights(COICOP_LEVELS, W1_BASKET, hierarchy)

    assert operational[JULY][FOOD] == pytest.approx(DOCUMENTED_PHI_FOOD, abs=5e-10)
    assert operational[JULY][FOOD] + operational[JULY][DRINK] == pytest.approx(1.0, abs=1e-12)

    results, _ = validate_bottom_up(COICOP_LEVELS, operational, hierarchy, operational=True)
    july = next(row for row in results if row["reference_date"] == JULY)
    assert july["residual"] == pytest.approx(DOCUMENTED_COICOP_RESIDUAL_PP, abs=5e-9)
    assert july["passed"] is True


def test_documented_segment_shares_and_residual() -> None:
    """04.5.2 Gas from its three consumption segments across a valid link."""
    operational = derive_segment_operational_weights(
        SEGMENT_LEVELS, SEGMENT_WEIGHTS, SEGMENT_HIERARCHY
    )
    for segment, documented in zip(SEGMENTS, DOCUMENTED_SEGMENT_PHI, strict=True):
        assert operational[JULY][segment] == pytest.approx(documented, abs=5e-10)
    assert sum(operational[JULY].values()) == pytest.approx(1.0, abs=1e-12)

    results, _ = validate_segment_bottom_up(
        SEGMENT_LEVELS, operational, SEGMENT_HIERARCHY, operational=True
    )
    assert len(results) == 1
    assert results[0]["residual"] == pytest.approx(DOCUMENTED_SEGMENT_RESIDUAL_PP, abs=5e-8)
    assert results[0]["passed"] is True


@pytest.mark.parametrize(
    "value",
    [
        "0.8866345377",
        "0.6157237353",
        "0.3505107961",
        "0.0337654685",
        "1.1470659776",
        "0.9999799375",
    ],
)
def test_the_documented_numbers_are_the_ones_asserted(value: str) -> None:
    """A reader must find in the document exactly what the tests pin."""
    assert value in _methodology()


def test_the_measured_residual_table_is_present() -> None:
    """The published residual table is evidence; it must not drift out silently."""
    text = _methodology()
    assert "## Measured residuals on the current published source" in text
    for figure in ("8,436", "8,251", "1,530", "1,275", "0.000273 pp", "0.336575 pp"):
        assert figure in text, figure
    # Every reconciliation row must report zero failures.
    rows = re.findall(r"^\| (?:W1|COICOP|Segment)[^|]*\|[^|]*\| *(\d+) *\|", text, re.MULTILINE)
    assert rows, "the residual table lost its rows"
    assert set(rows) == {"0"}, f"a documented check reports failures: {rows}"
