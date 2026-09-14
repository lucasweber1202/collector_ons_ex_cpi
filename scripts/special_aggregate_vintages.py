"""Discover MM23 snapshots and build source-backed special-weight regimes.

From 2017 onward ONS uses two CPI higher-level weight updates each year. The
current MM23 annual weight eventually represents the February-December regime.
The final January-regime value is preserved in the MM23 version that is
superseded by the scheduled March release. Nothing in this module persists data.
"""

from __future__ import annotations

import html
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin

from scripts.config import DOWNLOAD_DELAY
from scripts.special_aggregates import (
    EX_CPI_SPECIAL_AGGREGATES,
    MM23_DATASET_URL,
    MM23SpecialPanel,
    parse_mm23_special_aggregates,
    resolve_table38_alt_series,
)

MM23_VERSIONS_URL = (
    "https://www.ons.gov.uk/economy/inflationandpriceindices/datasets/consumerpriceindices/current"
)
DOUBLE_WEIGHT_START_YEAR = 2017
MM23_WEIGHT_DATASET = "ONS Consumer price inflation time series (MM23) special aggregate weights"


@dataclass(frozen=True)
class MM23Snapshot:
    """One archived full-MM23 CSV and the date on which ONS superseded it."""

    version_id: str
    csv_url: str
    superseded_at: datetime
    reason: str


class _TableRows(HTMLParser):
    """Capture text and links for each HTML table row without external parsers."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, list[str]]] = []
        self._in_row = False
        self._text: list[str] = []
        self._hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._in_row = True
            self._text = []
            self._hrefs = []
            return
        if not self._in_row or tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self._hrefs.append(value)

    def handle_data(self, data: str) -> None:
        if self._in_row:
            stripped = data.strip()
            if stripped:
                self._text.append(stripped)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "tr" or not self._in_row:
            return
        self.rows.append((" ".join(self._text), list(self._hrefs)))
        self._in_row = False
        self._text = []
        self._hrefs = []


_DATE_RE = re.compile(r"(\d{1,2}\s+[A-Za-z]+\s+\d{4}\s+\d{2}:\d{2})")
_VERSION_RE = re.compile(r"/previous/(v\d+)/mm23\.csv(?:$|[?&#])", re.IGNORECASE)


def _snapshot_reason(row_text: str) -> str:
    lowered = row_text.lower()
    if "scheduled update/revision" in lowered:
        return "scheduled"
    if "correction" in lowered:
        return "correction"
    return "other"


def parse_mm23_snapshot_index(page_html: str) -> list[MM23Snapshot]:
    """Parse versioned full-MM23 CSV links from the official dataset history page."""
    parser = _TableRows()
    parser.feed(page_html)
    snapshots: list[MM23Snapshot] = []
    seen_versions: set[str] = set()
    for row_text, hrefs in parser.rows:
        date_match = _DATE_RE.search(row_text)
        if date_match is None:
            continue
        superseded_at = datetime.strptime(date_match.group(1), "%d %B %Y %H:%M").replace(tzinfo=UTC)
        csv_candidates: list[tuple[str, str]] = []
        for raw_href in hrefs:
            href = html.unescape(raw_href)
            decoded = unquote(href)
            version_match = _VERSION_RE.search(decoded)
            if version_match is not None:
                csv_candidates.append((version_match.group(1).lower(), href))
        if not csv_candidates:
            continue
        if len(csv_candidates) != 1:
            raise ValueError(
                f"MM23 history row for {superseded_at.date()} has {len(csv_candidates)} CSV links"
            )
        version_id, href = csv_candidates[0]
        if version_id in seen_versions:
            raise ValueError(f"MM23 history publishes duplicate version {version_id}")
        seen_versions.add(version_id)
        snapshots.append(
            MM23Snapshot(
                version_id=version_id,
                csv_url=urljoin(MM23_VERSIONS_URL, href),
                superseded_at=superseded_at,
                reason=_snapshot_reason(row_text),
            )
        )
    if not snapshots:
        raise ValueError("ONS MM23 history page contains no versioned CSV snapshots")
    return sorted(snapshots, key=lambda snapshot: snapshot.superseded_at)


def january_regime_snapshots(
    snapshots: list[MM23Snapshot],
    start_year: int = DOUBLE_WEIGHT_START_YEAR,
) -> dict[int, MM23Snapshot]:
    """Select the final January-weight snapshot for each double-update year.

    The target is the version superseded by the *scheduled* March release. A
    same-day correction is deliberately not a selector because it belongs to
    the newly released February-December regime rather than the preceding
    January regime.
    """
    selected: dict[int, MM23Snapshot] = {}
    for snapshot in snapshots:
        year = snapshot.superseded_at.year
        if year < start_year or snapshot.superseded_at.month != 3:
            continue
        if snapshot.reason != "scheduled":
            continue
        if year in selected:
            previous = selected[year]
            raise ValueError(
                "Two scheduled March MM23 snapshots found for "
                f"{year}: {previous.version_id} at {previous.superseded_at.isoformat()} and "
                f"{snapshot.version_id} at {snapshot.superseded_at.isoformat()}"
            )
        selected[year] = snapshot
    return selected


def _official_weights(panel: MM23SpecialPanel, year: int) -> dict[str, float]:
    """Return reviewed exclusion and complement weights for one MM23 year or fail."""
    values = panel.annual_weights.get(year)
    if values is None:
        raise ValueError(f"MM23 panel contains no annual weights for {year}")
    weights: dict[str, float] = {}
    for aggregate in EX_CPI_SPECIAL_AGGREGATES:
        for cdid in (aggregate["weight_cdid"], aggregate["complement_weight_cdid"]):
            value = values.get(cdid)
            if value is None:
                raise ValueError(f"MM23 panel has no {cdid} official weight for {year}")
            weights[cdid] = value
    return weights


def build_exclusion_weight_regimes(
    current: MM23SpecialPanel,
    current_release_date: date,
    january_panels: Mapping[int, MM23SpecialPanel],
    *,
    start_year: int | None = None,
) -> dict[date, dict[str, float]]:
    """Expand published annual special weights onto only their valid CPI months.

    Before 2017 one annual weight regime applies to all months. From 2017, the
    January value comes from the final February-release snapshot and the final
    annual value applies to February-December. During a live February release,
    the current-year MM23 value is itself the January regime, so no future
    February-December rows are invented before the second update is published.

    For a historical double-update year, a missing January snapshot is an error
    rather than a reason to reuse the later February-December value.
    """
    expanded: dict[date, dict[str, float]] = {}
    years = sorted(current.annual_weights)
    if start_year is not None:
        years = [year for year in years if year >= start_year]
    for year in years:
        if year > current_release_date.year:
            continue
        current_weights = _official_weights(current, year)
        if year < DOUBLE_WEIGHT_START_YEAR:
            for month in range(1, 13):
                expanded[date(year, month, 1)] = dict(current_weights)
            continue

        if year == current_release_date.year and current_release_date.month < 2:
            continue
        if year == current_release_date.year and current_release_date.month == 2:
            expanded[date(year, 1, 1)] = dict(current_weights)
            continue

        january_panel = january_panels.get(year)
        if january_panel is None:
            raise ValueError(f"Missing archived January MM23 weight panel for {year}")
        expanded[date(year, 1, 1)] = _official_weights(january_panel, year)
        for month in range(2, 13):
            expanded[date(year, month, 1)] = dict(current_weights)
    return expanded


def map_exclusion_weight_regimes_to_table38(
    regimes: Mapping[date, Mapping[str, float]],
    catalog: Mapping[str, Mapping[str, str]],
) -> dict[date, dict[str, float]]:
    """Map MM23 weight CDIDs onto their existing Table 38 EX-CPI index targets.

    This target-ID view is useful for validation and joins. It is not the
    preferred persistence identity for `original_weights`, which preserves the source weight identifier via
    EXCPI_WEIGHT_NATIVE_<CDID>.
    """
    resolved, missing = resolve_table38_alt_series(catalog)
    if missing:
        raise ValueError(
            f"Cannot map MM23 exclusion weights; Table 38 EX-CPI index CDIDs missing: {missing}"
        )
    target_by_weight = {
        aggregate["weight_cdid"]: resolved[aggregate["index_cdid"]]
        for aggregate in EX_CPI_SPECIAL_AGGREGATES
    }
    mapped: dict[date, dict[str, float]] = {}
    for month, values in regimes.items():
        month_values: dict[str, float] = {}
        for weight_cdid, series_id in target_by_weight.items():
            value = values.get(weight_cdid)
            if value is None:
                raise ValueError(f"Missing {weight_cdid} MM23 exclusion weight at {month}")
            if series_id in month_values:
                raise ValueError(f"Two MM23 exclusion weights map to {series_id} at {month}")
            month_values[series_id] = value
        mapped[month] = month_values
    return mapped


def mm23_original_weight_id(weight_cdid: str) -> str:
    """Return the stable source identity used for a published MM23 weight."""
    native = re.sub(r"[^A-Z0-9]+", "", weight_cdid.strip().upper())
    if not native:
        raise ValueError("MM23 weight CDID is empty")
    return f"EXCPI_WEIGHT_NATIVE_{native}"


def build_mm23_original_weight_layer(
    regimes: Mapping[date, Mapping[str, float]],
    catalog: Mapping[str, Mapping[str, str]],
) -> tuple[dict[date, dict[str, float]], dict[str, dict[str, str]]]:
    """Build source-ID weights plus the audit crosswalk to Table 38 EX-CPI index series.

    The official source row keeps its own stable identifier in
    original_weights, while the audit map records the exact Table 38 target. The native MM23 weight CDID therefore remains
    auditable instead of being replaced by the related index CDID.
    """
    resolved, missing = resolve_table38_alt_series(catalog)
    if missing:
        raise ValueError(
            f"Cannot build MM23 original weights; Table 38 EX-CPI index CDIDs missing: {missing}"
        )
    audit: dict[str, dict[str, str]] = {}
    source_id_by_weight: dict[str, str] = {}
    for aggregate in EX_CPI_SPECIAL_AGGREGATES:
        for weight_cdid, suffix in (
            (aggregate["weight_cdid"], "exclusion"),
            (aggregate["complement_weight_cdid"], "complement"),
        ):
            source_id = mm23_original_weight_id(weight_cdid)
            source_id_by_weight[weight_cdid] = source_id
            audit[source_id] = {
                "code": weight_cdid,
                "name": f"{aggregate['label']} ({suffix} weight)",
                "native_id": weight_cdid,
                "mapped_series_id": resolved[aggregate["index_cdid"]],
                "dataset": MM23_WEIGHT_DATASET,
                "source_url": MM23_DATASET_URL,
            }

    originals: dict[date, dict[str, float]] = {}
    for month, values in regimes.items():
        month_values: dict[str, float] = {}
        for weight_cdid, source_id in source_id_by_weight.items():
            value = values.get(weight_cdid)
            if value is None:
                raise ValueError(f"Missing {weight_cdid} MM23 exclusion weight at {month}")
            month_values[source_id] = value
        originals[month] = month_values
    return originals, audit


def discover_mm23_snapshots() -> list[MM23Snapshot]:
    """Fetch and parse the official full-MM23 previous-version index."""
    from scripts.extract import build_client, http_get

    with build_client() as client:
        response = http_get(client, MM23_VERSIONS_URL)
    return parse_mm23_snapshot_index(response.text)


def collect_mm23_snapshot(snapshot: MM23Snapshot) -> MM23SpecialPanel:
    """Download one archived full-MM23 CSV through the collector HTTP policy."""
    from scripts.extract import build_client, http_get

    with build_client() as client:
        response = http_get(client, snapshot.csv_url)
    return parse_mm23_special_aggregates(response.content)


def collect_january_weight_panels(
    snapshots: Mapping[int, MM23Snapshot],
    *,
    start_year: int,
    end_year: int,
) -> dict[int, MM23SpecialPanel]:
    """Download a bounded range of archived January-regime MM23 panels.

    This is intentionally separate from the normal monthly collection path.
    Full-MM23 snapshots are large, so historical backfill is explicit, bounded
    by year, reuses one HTTP client and observes the collector download delay.
    A requested double-update year with no scheduled-March snapshot fails rather
    than silently shortening the backfill.
    """
    if end_year < start_year:
        raise ValueError(f"Invalid MM23 snapshot year range: {start_year}..{end_year}")
    required_years = list(range(max(start_year, DOUBLE_WEIGHT_START_YEAR), end_year + 1))
    missing = [year for year in required_years if year not in snapshots]
    if missing:
        raise ValueError(f"Missing scheduled-March MM23 snapshots for years: {missing}")
    if not required_years:
        return {}

    from scripts.extract import build_client, http_get

    panels: dict[int, MM23SpecialPanel] = {}
    with build_client() as client:
        for position, year in enumerate(required_years):
            if position and DOWNLOAD_DELAY:
                time.sleep(DOWNLOAD_DELAY)
            response = http_get(client, snapshots[year].csv_url)
            panel = parse_mm23_special_aggregates(response.content)
            if year not in panel.annual_weights:
                raise ValueError(
                    f"Archived MM23 snapshot {snapshots[year].version_id} has no weights for {year}"
                )
            panels[year] = panel
    return panels
