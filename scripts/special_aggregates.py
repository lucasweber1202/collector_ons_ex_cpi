"""Reviewed ONS MM23 crosswalk and source checks for CPI exclusion aggregates.

This module deliberately does not persist MM23 weights yet. The source publishes
those weights as annual observations while this collector's existing
``original_weights`` contract is reference-month based. Until that storage
semantics is explicitly resolved, MM23 is used as a source-verified identity and
validation layer over Table 38 ``ALT`` series.
"""

from __future__ import annotations

import io
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import TypedDict

import pandas as pd

MM23_DATASET_URL = (
    "https://www.ons.gov.uk/economy/inflationandpriceindices/datasets/consumerpriceindices"
)
MM23_DOWNLOAD_URL = (
    "https://www.ons.gov.uk/file?uri=%2Feconomy%2Finflationandpriceindices%2Fdatasets%2F"
    "consumerpriceindices%2Fcurrent%2Fmm23.csv"
)
MM23_WEIGHT_TOTAL = 1000.0
MM23_WEIGHT_SUM_TOLERANCE = 0.01

_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


class SpecialAggregate(TypedDict):
    """Native ONS identifiers that describe one CPI exclusion aggregate."""

    label: str
    weight_cdid: str
    index_cdid: str
    rate_12m_cdid: str
    complement_weight_cdid: str
    complement_index_cdid: str
    complement_rate_12m_cdid: str


@dataclass(frozen=True)
class MM23SpecialPanel:
    """Only the reviewed MM23 special-aggregate observations needed for checks."""

    annual_weights: dict[int, dict[str, float]]
    monthly_indices: dict[date, dict[str, float]]
    monthly_rates_12m: dict[date, dict[str, float]]


# Every CDID below was verified against ONS MM23 pages. The complement is the
# published component removed from the exclusion index. Where both annual
# weights are published for the same year they provide a direct 1,000-parts
# source-level reconciliation, without reconstructing the basket from names.
EX_CPI_SPECIAL_AGGREGATES: tuple[SpecialAggregate, ...] = (
    {
        "label": "CPI excluding tobacco",
        "weight_cdid": "A9F5",
        "index_cdid": "DK9V",
        "rate_12m_cdid": "DKL7",
        "complement_weight_cdid": "CJWP",
        "complement_index_cdid": "D7CB",
        "complement_rate_12m_cdid": "D7GN",
    },
    {
        "label": "CPI excluding energy",
        "weight_cdid": "A9FT",
        "index_cdid": "DKC5",
        "rate_12m_cdid": "DKO7",
        "complement_weight_cdid": "A9F3",
        "complement_index_cdid": "DK9T",
        "complement_rate_12m_cdid": "DKL5",
    },
    {
        "label": "CPI excluding energy, food, alcohol and tobacco",
        "weight_cdid": "A9FU",
        "index_cdid": "DKC6",
        "rate_12m_cdid": "DKO8",
        "complement_weight_cdid": "A9G4",
        "complement_index_cdid": "DKD6",
        "complement_rate_12m_cdid": "DKP8",
    },
    {
        "label": "CPI excluding energy and unprocessed food",
        "weight_cdid": "A9FV",
        "index_cdid": "DKC7",
        "rate_12m_cdid": "DKO9",
        "complement_weight_cdid": "A9G5",
        "complement_index_cdid": "DKD7",
        "complement_rate_12m_cdid": "DKP9",
    },
    {
        "label": "CPI excluding seasonal food",
        "weight_cdid": "A9FW",
        "index_cdid": "DKC8",
        "rate_12m_cdid": "DKP2",
        "complement_weight_cdid": "A9EZ",
        "complement_index_cdid": "DK9R",
        "complement_rate_12m_cdid": "DKL3",
    },
    {
        "label": "CPI excluding energy and seasonal food",
        "weight_cdid": "A9FX",
        "index_cdid": "DKC9",
        "rate_12m_cdid": "DKP3",
        "complement_weight_cdid": "A9G6",
        "complement_index_cdid": "DKD8",
        "complement_rate_12m_cdid": "DKQ2",
    },
    {
        "label": "CPI excluding alcohol and tobacco",
        "weight_cdid": "A9FY",
        "index_cdid": "DKD2",
        "rate_12m_cdid": "DKP4",
        "complement_weight_cdid": "CHZS",
        "complement_index_cdid": "D7BV",
        "complement_rate_12m_cdid": "D7G9",
    },
    {
        "label": "CPI excluding liquid fuels, vehicle fuels and lubricants",
        "weight_cdid": "A9FZ",
        "index_cdid": "DKD3",
        "rate_12m_cdid": "DKP5",
        "complement_weight_cdid": "A9FS",
        "complement_index_cdid": "DKC4",
        "complement_rate_12m_cdid": "DKO6",
    },
    {
        "label": "CPI excluding housing, water, electricity, gas and other fuels",
        "weight_cdid": "A9G2",
        "index_cdid": "DKD4",
        "rate_12m_cdid": "DKP6",
        "complement_weight_cdid": "CHZU",
        "complement_index_cdid": "D7BX",
        "complement_rate_12m_cdid": "D7GB",
    },
    {
        "label": "CPI excluding education, health and social protection",
        "weight_cdid": "A9G3",
        "index_cdid": "DKD5",
        "rate_12m_cdid": "DKP7",
        "complement_weight_cdid": "A9G7",
        "complement_index_cdid": "DKD9",
        "complement_rate_12m_cdid": "DKQ3",
    },
)


def validate_crosswalk() -> None:
    """Fail if a reviewed native identifier is accidentally duplicated."""
    native_fields = (
        ("weight_cdid", [row["weight_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES]),
        ("index_cdid", [row["index_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES]),
        ("rate_12m_cdid", [row["rate_12m_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES]),
        (
            "complement_weight_cdid",
            [row["complement_weight_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES],
        ),
        (
            "complement_index_cdid",
            [row["complement_index_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES],
        ),
        (
            "complement_rate_12m_cdid",
            [row["complement_rate_12m_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES],
        ),
    )
    for field, values in native_fields:
        if len(values) != len(set(values)):
            raise ValueError(f"Duplicate MM23 {field} in ex-CPI crosswalk")
    for row in EX_CPI_SPECIAL_AGGREGATES:
        if row["weight_cdid"] == row["complement_weight_cdid"]:
            raise ValueError(f"Exclusion and complement weights collide: {row['label']}")


def required_mm23_cdids() -> frozenset[str]:
    """Return every native MM23 series required by the reviewed checks."""
    values: set[str] = set()
    values.update(row["weight_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES)
    values.update(row["index_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES)
    values.update(row["rate_12m_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES)
    values.update(row["complement_weight_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES)
    values.update(row["complement_index_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES)
    values.update(row["complement_rate_12m_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES)
    return frozenset(values)


def resolve_table38_alt_series(
    catalog: Mapping[str, Mapping[str, str]],
) -> tuple[dict[str, str], list[str]]:
    """Resolve reviewed MM23 index CDIDs onto already-collected Table 38 ALT IDs.

    Returns ``(resolved, missing)`` where ``resolved`` maps the MM23 index CDID
    to this collector's stable ``series_id``. Resolution is exact on the native
    ONS CDID and only accepts the ``ALT`` family; no name/fuzzy fallback exists.
    Missing rows are reported rather than fabricated so the caller can decide
    whether a source release changed scope.
    """
    by_native: dict[str, list[str]] = {}
    for series_id, fields in catalog.items():
        if fields.get("family") != "ALT":
            continue
        native_id = str(fields.get("native_id", "")).upper()
        if native_id:
            by_native.setdefault(native_id, []).append(series_id)

    resolved: dict[str, str] = {}
    missing: list[str] = []
    for row in EX_CPI_SPECIAL_AGGREGATES:
        native_id = row["index_cdid"]
        candidates = by_native.get(native_id, [])
        if len(candidates) > 1:
            raise ValueError(f"Table 38 publishes duplicate ALT CDID {native_id}: {candidates}")
        if not candidates:
            missing.append(native_id)
            continue
        resolved[native_id] = candidates[0]
    return resolved, missing


def _finite_float(value: object) -> float | None:
    """Parse one MM23 cell, treating ONS missing markers as absent."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "<na>"} or text == "..":
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _month_period(value: str) -> date | None:
    """Parse an MM23 monthly period such as ``2026 JUL`` without locale state."""
    match = re.fullmatch(r"(\d{4}) ([A-Z]{3})", value)
    if not match:
        return None
    month = _MONTHS.get(match.group(2))
    if month is None:
        return None
    return date(int(match.group(1)), month, 1)


def _selected_values(row: pd.Series, cdids: frozenset[str]) -> dict[str, float]:
    """Read finite values for a reviewed CDID set from one wide MM23 row."""
    values: dict[str, float] = {}
    for cdid in cdids:
        number = _finite_float(row.get(cdid))
        if number is not None:
            values[cdid] = number
    return values


def parse_mm23_special_aggregates(blob: bytes) -> MM23SpecialPanel:
    """Parse only reviewed ex-CPI columns from the wide official MM23 CSV.

    The ONS file starts with a title row. After skipping it, the next row is the
    CDID header. Annual rows use ``YYYY`` and monthly rows use ``YYYY MON`` in
    the first ``CDID`` column. Quarterly rows and unrelated series are ignored.
    """
    try:
        frame = pd.read_csv(io.BytesIO(blob), skiprows=1, dtype=str, low_memory=False)
    except (UnicodeDecodeError, pd.errors.ParserError) as exc:
        raise ValueError(f"Could not parse ONS MM23 CSV: {exc}") from exc
    normalized = [str(column).strip().upper() for column in frame.columns]
    if len(normalized) != len(set(normalized)):
        raise ValueError("MM23 CSV contains duplicate columns after CDID normalization")
    frame.columns = normalized
    if "CDID" not in frame.columns:
        raise ValueError("MM23 CSV has no CDID period column after the title row")

    required = required_mm23_cdids()
    missing = sorted(required - set(normalized))
    if missing:
        raise ValueError(f"MM23 CSV is missing reviewed ex-CPI CDIDs: {missing}")

    weight_cdids = frozenset(
        [row["weight_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES]
        + [row["complement_weight_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES]
    )
    index_cdids = frozenset(
        [row["index_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES]
        + [row["complement_index_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES]
    )
    rate_cdids = frozenset(
        [row["rate_12m_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES]
        + [row["complement_rate_12m_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES]
    )

    annual_weights: dict[int, dict[str, float]] = {}
    monthly_indices: dict[date, dict[str, float]] = {}
    monthly_rates: dict[date, dict[str, float]] = {}
    for _, row in frame.iterrows():
        period = str(row.get("CDID", "")).strip().upper()
        if re.fullmatch(r"\d{4}", period):
            values = _selected_values(row, weight_cdids)
            if values:
                annual_weights[int(period)] = values
            continue
        month = _month_period(period)
        if month is None:
            continue
        indices = _selected_values(row, index_cdids)
        rates = _selected_values(row, rate_cdids)
        if indices:
            monthly_indices[month] = indices
        if rates:
            monthly_rates[month] = rates

    if not annual_weights:
        raise ValueError("MM23 CSV contains no reviewed annual special-aggregate weights")
    if not monthly_indices:
        raise ValueError("MM23 CSV contains no reviewed monthly special-aggregate indices")
    if not monthly_rates:
        raise ValueError("MM23 CSV contains no reviewed monthly special-aggregate 12m rates")
    return MM23SpecialPanel(annual_weights, monthly_indices, monthly_rates)


def collect_mm23_special_aggregates() -> MM23SpecialPanel:
    """Download the current official MM23 CSV through the collector HTTP policy."""
    # Local import keeps this module independently testable and avoids making
    # extraction import this validation layer merely to build the Table 38 map.
    from scripts.extract import build_client, http_get

    with build_client() as client:
        response = http_get(client, MM23_DOWNLOAD_URL)
    return parse_mm23_special_aggregates(response.content)


def complement_weight_checks(
    panel: MM23SpecialPanel,
    tolerance: float = MM23_WEIGHT_SUM_TOLERANCE,
    *,
    latest_only: bool = False,
) -> list[dict[str, object]]:
    """Compare each published exclusion weight with its removed-component weight."""
    checks: list[dict[str, object]] = []
    for aggregate in EX_CPI_SPECIAL_AGGREGATES:
        available = [
            year
            for year, values in panel.annual_weights.items()
            if aggregate["weight_cdid"] in values
            and aggregate["complement_weight_cdid"] in values
        ]
        years = [max(available)] if available and latest_only else sorted(available)
        for year in years:
            values = panel.annual_weights[year]
            exclusion = values[aggregate["weight_cdid"]]
            complement = values[aggregate["complement_weight_cdid"]]
            total = exclusion + complement
            residual = total - MM23_WEIGHT_TOTAL
            checks.append(
                {
                    "label": aggregate["label"],
                    "year": year,
                    "exclusion_weight": exclusion,
                    "complement_weight": complement,
                    "total": total,
                    "residual": residual,
                    "passed": abs(residual) <= tolerance,
                }
            )
    return checks


validate_crosswalk()
