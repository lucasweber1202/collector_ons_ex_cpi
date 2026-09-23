"""GUIDELINES 5.1 usable-series filtering for the EX-CPI target.

The rule is the CPI collector's, but the protection that makes it safe cannot
be copied from there, because the two repositories key their weights
differently. In CPI a series carries its own weight under its own identity, so
"is this series_id in the current weight regime" answers the question directly.
Here it does not: an index is stored as ``EXCPI_INDEX_NATIVE_<CDID>`` while its
weight is ``EXCPI_WEIGHT_NATIVE_<other CDID>``, a different identity entirely.
Asking the CPI question of EX-CPI would find nothing and protect nothing.

So protection goes through the published crosswalk instead: an index is
load-bearing when the aggregate it belongs to still has a weight in the
current regime, looked up by weight CDID. To be dropped, an aggregate must
have been retired by ONS on both sides -- index and weight -- which is what
retirement actually looks like in MM23.

As in CPI, no minimum-history rule is applied: ONS can introduce a new
exclusion aggregate at any release, and a new aggregate is precisely what a
forecast target wants rather than something to discard.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from scripts.config import MAX_STALE_MONTHS, MIN_HISTORY_YEARS
from scripts.special_aggregates import EX_CPI_SPECIAL_AGGREGATES

logger = logging.getLogger(__name__)

Observations = dict[date, dict[str, float | None]]


@dataclass(frozen=True)
class UsabilityReport:
    """Which EX-CPI aggregates survived 5.1, and why the others did not."""

    kept: tuple[str, ...]
    stale: tuple[str, ...]
    empty: tuple[str, ...]
    short_history: tuple[str, ...]
    protected_by_weight: tuple[str, ...]

    @property
    def dropped(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.stale) | set(self.empty) | set(self.short_history)))


def _month_distance(later: date, earlier: date) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def _weighted_index_cdids(
    weight_rows: list[dict[str, Any]], latest_period: date, max_stale_months: int
) -> set[str]:
    """Index CDIDs whose aggregate still carries a *current* weight.

    "Current" is measured against the observation period, not against the
    newest weight row present. Taking the newest row instead would make a
    weight regime that stopped years ago define its own currency, and a
    retired aggregate whose weight stopped with it would be protected
    permanently -- the filter could then never fire at all.
    """
    if not weight_rows:
        return set()
    live_weight_ids = {
        str(row["series_id"])
        for row in weight_rows
        if _month_distance(latest_period, row["reference_date"]) <= max_stale_months
    }
    weighted: set[str] = set()
    for aggregate in EX_CPI_SPECIAL_AGGREGATES:
        # The stored weight identity is EXCPI_WEIGHT_NATIVE_<weight cdid>; any
        # id ending in that CDID belongs to this aggregate.
        if any(sid.endswith(aggregate["weight_cdid"]) for sid in live_weight_ids):
            weighted.add(aggregate["index_cdid"])
    return weighted


def classify_series(
    observations: Observations,
    weight_rows: list[dict[str, Any]],
    *,
    latest_period: date,
    max_stale_months: int = MAX_STALE_MONTHS,
    min_history_years: float = MIN_HISTORY_YEARS,
) -> UsabilityReport:
    """Decide, per stored index series_id, whether 5.1 admits it.

    Both halves of 5.1 apply: obsolete (last print older than
    max_stale_months) and insufficient history (non-null span shorter than
    min_history_years). The second only fires when the aggregate is also
    unprotected by the crosswalk and no longer printing, so a newly introduced
    aggregate -- short by definition -- is never reached.
    """
    weighted_cdids = _weighted_index_cdids(weight_rows, latest_period, max_stale_months)
    series_ids = {sid for values in observations.values() for sid in values}
    kept: list[str] = []
    stale: list[str] = []
    empty: list[str] = []
    short: list[str] = []
    protected: list[str] = []
    for series_id in sorted(series_ids):
        months = [m for m, values in observations.items() if values.get(series_id) is not None]
        last_seen = max(months) if months else None
        span_years = (max(months) - min(months)).days / 365.25 if len(months) > 1 else 0.0
        is_weighted = any(series_id.endswith(cdid) for cdid in weighted_cdids)
        if is_weighted:
            kept.append(series_id)
            if last_seen is None or _month_distance(latest_period, last_seen) > max_stale_months:
                protected.append(series_id)
            continue
        if last_seen is None:
            empty.append(series_id)
            continue
        if _month_distance(latest_period, last_seen) > max_stale_months:
            stale.append(series_id)
            continue
        if last_seen != latest_period and span_years < min_history_years:
            short.append(series_id)
            continue
        kept.append(series_id)
    return UsabilityReport(
        kept=tuple(kept),
        stale=tuple(stale),
        empty=tuple(empty),
        short_history=tuple(short),
        protected_by_weight=tuple(protected),
    )


def apply_usable_series_filter(
    observations: Observations,
    catalog: Mapping[str, dict[str, str]],
    weight_rows: list[dict[str, Any]],
    *,
    latest_period: date,
    max_stale_months: int = MAX_STALE_MONTHS,
    min_history_years: float = MIN_HISTORY_YEARS,
) -> tuple[Observations, dict[str, dict[str, str]], UsabilityReport]:
    """Prune unusable aggregates from what is about to be written.

    Only what the filter judged unusable is removed; anything it did not
    classify is left untouched, so weight rows for series absent from the
    observation set are never deleted as collateral.
    """
    report = classify_series(
        observations,
        weight_rows,
        latest_period=latest_period,
        max_stale_months=max_stale_months,
        min_history_years=min_history_years,
    )
    drop = set(report.dropped)
    logger.info(
        "Usable-series filter: kept %d, dropped %d (stale=%d empty=%d "
        "short_history=%d; max_stale_months=%d, min_history_years=%s, "
        "latest_period=%s)",
        len(report.kept),
        len(report.dropped),
        len(report.stale),
        len(report.empty),
        len(report.short_history),
        max_stale_months,
        min_history_years,
        latest_period,
    )
    if report.protected_by_weight:
        logger.info(
            "Usable-series filter: %d stale series kept because their aggregate still "
            "carries a weight in the current regime",
            len(report.protected_by_weight),
        )
    if drop:
        logger.info("Usable-series filter dropped: %s", ", ".join(sorted(drop)))
    filtered: Observations = {}
    for month, values in observations.items():
        retained = {sid: value for sid, value in values.items() if sid not in drop}
        if retained:
            filtered[month] = retained
    pruned_catalog = {sid: f for sid, f in catalog.items() if sid not in drop}
    return filtered, pruned_catalog, report
