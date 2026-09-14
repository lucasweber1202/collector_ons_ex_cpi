"""Download and parse ONS consumption-segment CPI indices and their weights.

Consumption segments are the deepest level at which ONS publishes both a CPI
index and a CPI weight. They come from a different dataset than Table 38, carry
a different statistical status (research data, not accredited official
statistics) and use a different index reference, so they are parsed here rather
than inside ``scripts/extract.py``.

Two published layouts exist and both are handled explicitly:

* from the February 2026 edition the CSV carries ``COICOP4_ID``/``COICOP5_ID``
  columns, which are the authoritative classification for that month;
* earlier editions carry only ``CS_ID``, so the classification comes from the
  ONS CPI classification framework file of the same classification year.

Index reference: each segment index is re-referenced every January. Measured on
the full published panel, every month-on-month link whose two months are both
outside January reconciles with the published Table 38 parent (median absolute
residual 0.0003 percentage points), while links touching January do not
(median 3.4 and 0.6 percentage points). Segment links involving January are
therefore reported as unreconcilable rather than validated or repaired.
"""

from __future__ import annotations

import html
import io
import logging
import math
import re
import time
from datetime import date
from typing import NamedTuple
from urllib.parse import urljoin

import httpx
import pandas as pd

from scripts.config import DOWNLOAD_DELAY
from scripts.extract import (
    W1_ALIASES,
    build_client,
    http_get,
    expand_classification_code,
    make_series_id,
)

logger = logging.getLogger(__name__)

SEGMENTS_PAGE_URL = (
    "https://www.ons.gov.uk/economy/inflationandpriceindices/datasets/"
    "consumerpriceindicescpiandretailpricesindexrpiitemindicesandpricequotes"
)
SEGMENT_DATASET = "ONS consumption segment indices (CPI and RPI item indices and price quotes)"
SEGMENT_PROVENANCE = (
    "ONS publishes consumption segment indices as research data that are not "
    "accredited official statistics; the index is re-referenced every January"
)
# First edition published in the consumption-segment format.
SEGMENT_FIRST_MONTH = date(2025, 2, 1)

_EDITION_PATTERN = re.compile(
    r'href="(/file\?uri=[^"]*/consumptionsegmentindices[a-z0-9]+/'
    r'[^"]*consumptionsegments(\d{6})\.csv)"',
    re.IGNORECASE,
)
_FRAMEWORK_PATTERN = re.compile(
    r'href="(/file\?uri=[^"]*/cpi(20\d{2})classificationframework(revised)?/[^"]+\.(csv|xlsx))"',
    re.IGNORECASE,
)

# Published layouts, newest first. A file that matches neither is a schema drift
# and stops the run before anything is persisted.
_REQUIRED_COLUMNS = ("INDEX_DATE", "CS_ID", "CS_DESC", "CPI_INDEX", "CPI_WEIGHT")
_CLASSIFIED_COLUMNS = ("COICOP4_ID", "COICOP5_ID")
SEGMENT_MIN_ROWS = 300
SEGMENT_MIN_WEIGHT_TOTAL = 800.0
SEGMENT_MAX_WEIGHT_TOTAL = 1000.0 + 1e-9

# Reviewed subclass aliases. ONS splits class 07.1.1 into two published indices
# (new and second-hand cars) that the class code alone cannot separate; the
# consumption-segment subclass code does separate them.
SUBCLASS_ALIASES = {
    "07.1.1.1": "07.1.1A",
    "07.1.1.2": "07.1.1B",
    "07.1.81.0": "07.1.1A",
    "07.1.91.0": "07.1.1B",
}


class SegmentPanel(NamedTuple):
    """Everything one consumption-segment collection produces."""

    observations: dict[date, dict[str, float]]
    official_weights: dict[date, dict[str, float]]
    hierarchy: dict[date, dict[str, list[str]]]
    catalog: dict[str, dict[str, str]]


def segment_series_id(cs_id: str) -> str:
    """Return the stable identifier for one ONS consumption segment."""
    return make_series_id("CS", "SEG", cs_id)


def chain_year(month: date) -> int:
    """Return the ONS classification year a month belongs to (February to January)."""
    return month.year if month.month >= 2 else month.year - 1


def linkable(previous: date, current: date) -> bool:
    """Month-on-month links are only defined away from the January re-reference."""
    return previous.month != 1 and current.month != 1


def normalize_coicop(code: object, depth: int) -> str | None:
    """Normalize an ONS COICOP4/COICOP5 identifier into its dotted official code.

    ONS writes the same node two ways: ``CP0111``/``CP01113`` in the published
    CSV and zero-padded digits (``10101``/``1010103``) in the classification
    framework. A combined subclass such as ``CP01117_01118`` normalizes to its
    first component, which is what the weights workbook also uses.
    """
    if code is None:
        return None
    text = str(code).strip()
    if not text or text.lower() == "nan" or text == "0":
        return None
    if text.upper().startswith("CP"):
        digits = text[2:].split("_")[0]
        if depth == 4 and len(digits) == 4:
            return f"{digits[0:2]}.{digits[2]}.{digits[3]}"
        if depth == 5 and len(digits) == 5:
            return f"{digits[0:2]}.{digits[2]}.{digits[3]}.{digits[4]}"
        return None
    if not text.isdigit():
        return None
    if depth == 4:
        padded = text.zfill(6)
        if len(padded) != 6:
            return None
        return f"{padded[0:2]}.{int(padded[2:4])}.{int(padded[4:6])}"
    padded = text.zfill(8)
    if len(padded) != 8:
        return None
    return f"{padded[0:2]}.{int(padded[2:4])}.{int(padded[4:6])}.{int(padded[6:8])}"


class ParentResolver:
    """Resolve a consumption segment to its deepest published Table 38 ancestor.

    The resolution order is official classification code first, then the
    reviewed combined-code and subclass alias tables. Nothing is matched on
    names, and a code that reaches two Table 38 series raises instead of
    choosing a best candidate.
    """

    def __init__(self, catalog: dict[str, dict[str, str]], weight_codes: list[str]) -> None:
        self._by_code: dict[str, set[str]] = {}
        self._by_native: dict[str, str] = {}
        for series_id, fields in catalog.items():
            if fields["family"] != "COICOP":
                continue
            self._by_code.setdefault(fields["classification"], set()).add(series_id)
            self._by_native[fields["native_id"]] = series_id
        self._combined: dict[str, set[str]] = {}
        for code in weight_codes:
            if "/" not in code and code[-1:] not in ("A", "B"):
                continue
            for component in expand_classification_code(code):
                self._combined.setdefault(component, set()).add(code)

    def _series_for_weight_code(self, code: str) -> set[str]:
        native = W1_ALIASES.get(code)
        if native is not None:
            series_id = self._by_native.get(native)
            return {series_id} if series_id else set()
        return set(self._by_code.get(expand_classification_code(code)[0], set()))

    def series_for_code(self, code: str) -> set[str]:
        """Return every Table 38 series published for one classification code."""
        found = set(self._by_code.get(code, set()))
        for combined in self._combined.get(code, ()):
            found |= self._series_for_weight_code(combined)
        return found

    def resolve(self, coicop4: str | None, coicop5: str | None) -> str:
        """Return the parent series id, raising on ambiguous or unknown codes."""
        alias = SUBCLASS_ALIASES.get(coicop5 or "")
        if alias is not None:
            found = self._series_for_weight_code(alias)
            if len(found) == 1:
                return next(iter(found))
            raise ValueError(f"Reviewed subclass alias {coicop5} -> {alias} resolved to {found}")
        parts = (coicop4 or "").split(".")
        while parts:
            if parts[-1] == "0":
                parts = parts[:-1]
                continue
            found = self.series_for_code(".".join(parts))
            if len(found) == 1:
                return next(iter(found))
            if len(found) > 1:
                raise ValueError(
                    f"Ambiguous consumption-segment parent for {coicop4}/{coicop5}: {sorted(found)}"
                )
            parts = parts[:-1]
        raise ValueError(f"Unresolved consumption-segment parent for {coicop4}/{coicop5}")


def discover_segment_editions(page_html: str) -> dict[date, str]:
    """Map each published consumption-segment month to its official CSV URL."""
    editions: dict[date, str] = {}
    for href, stamp in _EDITION_PATTERN.findall(page_html):
        month = date(int(stamp[:4]), int(stamp[4:]), 1)
        url = urljoin(SEGMENTS_PAGE_URL, html.unescape(href))
        if editions.get(month, url) != url:
            raise ValueError(f"Two consumption-segment files published for {month}")
        editions[month] = url
    return editions


def discover_framework_editions(page_html: str) -> dict[int, str]:
    """Map each classification year to its official framework file, revised first."""
    best: dict[int, tuple[int, str]] = {}
    for href, year_text, revised, _ in _FRAMEWORK_PATTERN.findall(page_html):
        year = int(year_text)
        rank = 1 if revised else 0
        url = urljoin(SEGMENTS_PAGE_URL, html.unescape(href))
        if year not in best or rank > best[year][0]:
            best[year] = (rank, url)
    return {year: url for year, (_, url) in best.items()}


def _read_table(blob: bytes, url: str) -> pd.DataFrame:
    """Read a published CSV or XLSX framework/segment file as text columns."""
    if url.lower().endswith(".xlsx"):
        frame = pd.read_excel(io.BytesIO(blob), header=None, dtype=str, engine="openpyxl")
        frame.columns = [str(value).strip() for value in frame.iloc[0]]
        frame = frame.iloc[1:]
    else:
        frame = pd.read_csv(io.BytesIO(blob), dtype=str)
    frame.columns = [str(column).strip().upper() for column in frame.columns]
    return frame.apply(lambda column: column.astype(str).str.strip())


def parse_segment_csv(blob: bytes, month: date, url: str = "") -> pd.DataFrame:
    """Parse and schema-gate one consumption-segment edition."""
    frame = _read_table(blob, url)
    missing = [column for column in _REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Consumption-segment file for {month} is missing columns {missing}")
    if len(frame) < SEGMENT_MIN_ROWS:
        raise ValueError(
            f"Consumption-segment file for {month} has {len(frame)} rows, "
            f"below the {SEGMENT_MIN_ROWS} floor"
        )
    stamps = set(frame["INDEX_DATE"].unique())
    expected = month.strftime("%Y%m")
    if stamps != {expected}:
        raise ValueError(
            f"Consumption-segment file for {month} carries INDEX_DATE {sorted(stamps)}"
        )
    frame["CPI_INDEX"] = pd.to_numeric(frame["CPI_INDEX"], errors="coerce")
    frame["CPI_WEIGHT"] = pd.to_numeric(frame["CPI_WEIGHT"], errors="coerce")
    frame = frame[frame["CPI_INDEX"].notna() & (frame["CPI_WEIGHT"] > 0)]
    total = float(frame["CPI_WEIGHT"].sum())
    if not SEGMENT_MIN_WEIGHT_TOTAL <= total <= SEGMENT_MAX_WEIGHT_TOTAL:
        raise ValueError(
            f"Consumption-segment weights for {month} sum to {total:.3f} parts per thousand"
        )
    if (frame["CPI_INDEX"] <= 0).any():
        raise ValueError(f"Consumption-segment file for {month} carries a non-positive index")
    if frame["CS_ID"].duplicated().any():
        duplicates = sorted(frame.loc[frame["CS_ID"].duplicated(), "CS_ID"].unique())
        raise ValueError(f"Consumption-segment file for {month} repeats {duplicates}")
    return frame


def _framework_classification(frame: pd.DataFrame, month: date) -> dict[str, tuple[str, str]]:
    """Return ``cs_id -> (coicop4, coicop5)`` valid in ``month`` from a framework file."""
    for column in ("CS_ID", "COICOP4_ID", "COICOP5_ID", "START_DATE", "END_DATE"):
        if column not in frame.columns:
            raise ValueError(f"CPI classification framework is missing column {column}")
    stamp = month.strftime("%Y%m")
    valid = frame[(frame["START_DATE"] <= stamp) & (frame["END_DATE"] >= stamp)]
    resolved: dict[str, set[tuple[str, str]]] = {}
    for cs_id, coicop4, coicop5 in zip(
        valid["CS_ID"], valid["COICOP4_ID"], valid["COICOP5_ID"], strict=True
    ):
        pair = (
            normalize_coicop(coicop4, 4) or "",
            normalize_coicop(coicop5, 5) or "",
        )
        resolved.setdefault(cs_id, set()).add(pair)
    conflicting = sorted(cs_id for cs_id, pairs in resolved.items() if len(pairs) > 1)
    if conflicting:
        raise ValueError(
            f"CPI classification framework gives {month} more than one classification "
            f"for {conflicting[:5]}"
        )
    return {cs_id: next(iter(pairs)) for cs_id, pairs in resolved.items()}


def _classification_for_month(
    frame: pd.DataFrame, month: date, framework: pd.DataFrame | None
) -> dict[str, tuple[str | None, str | None]]:
    """Return each segment's official classification for one published month."""
    if all(column in frame.columns for column in _CLASSIFIED_COLUMNS):
        return {
            cs_id: (normalize_coicop(coicop4, 4), normalize_coicop(coicop5, 5))
            for cs_id, coicop4, coicop5 in zip(
                frame["CS_ID"], frame["COICOP4_ID"], frame["COICOP5_ID"], strict=True
            )
        }
    if framework is None:
        raise ValueError(
            f"Consumption-segment file for {month} carries no classification columns and "
            "no CPI classification framework was published for its classification year"
        )
    # Framework validity is per month, not per year: a segment reclassified mid
    # year must be read at the month it is being classified for.
    classified = _framework_classification(framework, month)
    missing = sorted(set(frame["CS_ID"]) - set(classified))
    if missing:
        raise ValueError(
            f"CPI classification framework does not classify {len(missing)} segments "
            f"published for {month}: {missing[:5]}"
        )
    return {
        cs_id: (classified[cs_id][0] or None, classified[cs_id][1] or None)
        for cs_id in frame["CS_ID"]
    }


def build_panel(
    months: dict[date, pd.DataFrame],
    frameworks: dict[int, pd.DataFrame],
    resolver: ParentResolver,
) -> SegmentPanel:
    """Turn parsed editions into observations, official weights and a hierarchy."""
    observations: dict[date, dict[str, float]] = {}
    official: dict[date, dict[str, float]] = {}
    hierarchy: dict[date, dict[str, list[str]]] = {}
    catalog: dict[str, dict[str, str]] = {}
    for month, frame in sorted(months.items()):
        classification = _classification_for_month(frame, month, frameworks.get(chain_year(month)))
        month_values: dict[str, float] = {}
        month_weights: dict[str, float] = {}
        month_children: dict[str, list[str]] = {}
        for cs_id, description, index, weight in zip(
            frame["CS_ID"], frame["CS_DESC"], frame["CPI_INDEX"], frame["CPI_WEIGHT"], strict=True
        ):
            coicop4, coicop5 = classification[cs_id]
            parent = resolver.resolve(coicop4, coicop5)
            series_id = segment_series_id(cs_id)
            value = float(index)
            if not math.isfinite(value):
                continue
            month_values[series_id] = value
            month_weights[series_id] = float(weight)
            month_children.setdefault(parent, []).append(series_id)
            catalog[series_id] = {
                "family": "CS",
                "node": "SEG",
                "level": "consumption_segment",
                "native_id": cs_id.upper(),
                "name": str(description).strip(),
                "classification": coicop5 or coicop4 or "",
                "dataset": SEGMENT_DATASET,
                "source_url": SEGMENTS_PAGE_URL,
                "provenance": SEGMENT_PROVENANCE,
                "parent_series_id": parent,
            }
        observations[month] = month_values
        official[month] = month_weights
        hierarchy[month] = {parent: sorted(children) for parent, children in month_children.items()}
    return SegmentPanel(observations, official, hierarchy, catalog)


def collect_segments(
    start_date: date | None,
    catalog: dict[str, dict[str, str]],
    weight_codes: list[str],
) -> SegmentPanel:
    """Download every published consumption-segment edition inside the window."""
    resolver = ParentResolver(catalog, weight_codes)
    floor = SEGMENT_FIRST_MONTH
    if start_date is not None:
        floor = max(start_date.replace(day=1), SEGMENT_FIRST_MONTH)
    with build_client() as client:
        page = http_get(client, SEGMENTS_PAGE_URL).text
        editions = {
            month: url for month, url in discover_segment_editions(page).items() if month >= floor
        }
        if not editions:
            logger.info("No consumption-segment edition published on or after %s", floor)
            return SegmentPanel({}, {}, {}, {})
        logger.info("Collecting %d consumption-segment editions from %s", len(editions), floor)
        framework_urls = discover_framework_editions(page)
        months: dict[date, pd.DataFrame] = {}
        for position, (month, url) in enumerate(sorted(editions.items()), start=1):
            time.sleep(DOWNLOAD_DELAY)
            try:
                blob = http_get(client, url).content
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
                # A listed edition that is withdrawn or not yet uploaded is a
                # soft failure: the next run's revision rewind covers the same
                # month again. Layout drift stays a hard failure below.
                logger.warning("ONS has no file for the %s consumption-segment edition", month)
                continue
            months[month] = parse_segment_csv(blob, month, url)
            if position % 10 == 0 or position == len(editions):
                logger.info(
                    "Downloaded %d/%d consumption-segment editions", position, len(editions)
                )
        needed = {
            chain_year(month)
            for month, frame in months.items()
            if not all(column in frame.columns for column in _CLASSIFIED_COLUMNS)
        }
        frameworks: dict[int, pd.DataFrame] = {}
        for year in sorted(needed):
            framework_url = framework_urls.get(year)
            if framework_url is None:
                raise ValueError(f"ONS published no CPI classification framework for {year}")
            time.sleep(DOWNLOAD_DELAY)
            frameworks[year] = _read_table(http_get(client, framework_url).content, framework_url)
    panel = build_panel(months, frameworks, resolver)
    logger.info(
        "Parsed %d consumption segments across %d months (%s to %s)",
        len({series_id for values in panel.observations.values() for series_id in values}),
        len(panel.observations),
        min(panel.observations, default="-"),
        max(panel.observations, default="-"),
    )
    return panel
