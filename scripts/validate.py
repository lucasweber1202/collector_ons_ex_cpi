"""Reconcile published UK CPI aggregates from their immediate published children.

Three layers are reconciled, each against the level ONS actually publishes:

* COICOP aggregates from their immediate COICOP children (Table 38 and W1);
* the same reconstruction from the derived operational shares that are stored;
* Table 38 classes and groups from their consumption segments.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from datetime import date
from itertools import pairwise
from typing import Any

from scripts.config import (
    MIN_SEGMENT_WEIGHT_RATIO,
    SEGMENT_TOLERANCE_PP,
    VALIDATION_TOLERANCE_PP,
)
from scripts.segments import linkable

logger = logging.getLogger(__name__)

# W1 only publishes weights from 2008, so earlier months are not reconcilable at
# source. They are reported separately and excluded from the coverage
# denominator; every other skip reason means a check that should have run.
SKIP_OUTSIDE_WEIGHTS_WINDOW = "month outside published weights window"
SKIP_MISSING_WEIGHT_MONTH = "missing weights inside published source window"
SKIP_MISSING_OBSERVATION_MONTH = "nonconsecutive observation months"
SKIP_PARENT_WITHOUT_WEIGHT = "parent series has no weight"
SKIP_CHILD_WITHOUT_WEIGHT = "child series has no weight"
SKIP_PARENT_WITHOUT_OBSERVATION = "parent series has no usable observation"
SKIP_CHILD_WITHOUT_OBSERVATION = "child series has no usable observation"
SKIP_PRICE_REFERENCE_MISSING = "price reference month not extracted"
SKIP_ZERO_WEIGHT_TOTAL = "price-updated child weights sum to zero"
# ONS re-references consumption-segment indices every January, so a link whose
# two months straddle that reset is not defined at source. It is reported
# separately and excluded from the coverage denominator, exactly like the
# pre-2008 weight window.
SKIP_SEGMENT_CHAIN_LINK = "segment link crosses the January index re-reference"
SKIP_SEGMENT_COMPOSITION = "segment set changed between the two months"

UNRECONCILABLE_AT_SOURCE = frozenset({SKIP_OUTSIDE_WEIGHTS_WINDOW, SKIP_SEGMENT_CHAIN_LINK})


def _observed(values: dict[str, float | None], series_id: str) -> float:
    """Return a level the caller's guard has already proven usable.

    Every arithmetic site below runs after a guard that excludes missing and
    zero levels. Reading through this helper states that invariant once, and
    turns a silent None leaking into the arithmetic into an immediate error
    rather than a nonsensical residual.
    """
    value = values.get(series_id)
    if value is None:
        raise ValueError(f"{series_id} has no usable observation at this point")
    return float(value)


def build_hierarchy(catalog: dict[str, dict[str, str]]) -> dict[str, list[str]]:
    """Build the parent-to-immediate-children map from verified upstream fields.

    ``parent_series_id`` is resolved during extraction from official
    classification codes, so the hierarchy here is a direct read of the source
    structure rather than a second, independent guess at it.
    """
    hierarchy: dict[str, list[str]] = {}
    for series_id, fields in catalog.items():
        if fields.get("family") != "COICOP":
            continue
        parent = fields.get("parent_series_id") or ""
        if not parent:
            continue
        if parent not in catalog:
            raise ValueError(f"{series_id} names unknown parent {parent}")
        hierarchy.setdefault(parent, []).append(series_id)
    return {parent: sorted(children) for parent, children in hierarchy.items()}


def validate_weight_sums(
    weights_by_date: dict[date, dict[str, float]],
    hierarchy: dict[str, list[str]],
    tolerance: float = 0.01,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Check that immediate child basket weights sum to each parent weight.

    Returns the performed checks and a count of skipped checks by reason, so a
    run that reconciles nothing cannot be mistaken for a run that reconciles
    everything.
    """
    results: list[dict[str, Any]] = []
    skips: Counter[str] = Counter()
    for ref_date, weights in sorted(weights_by_date.items()):
        for parent, children in hierarchy.items():
            if parent not in weights:
                skips[SKIP_PARENT_WITHOUT_WEIGHT] += 1
                continue
            if any(child not in weights for child in children):
                skips[SKIP_CHILD_WITHOUT_WEIGHT] += 1
                continue
            parent_weight = weights[parent]
            child_sum = sum(weights[child] for child in children)
            residual = child_sum - parent_weight
            results.append(
                {
                    "check": "weight_sum",
                    "reference_date": ref_date,
                    "parent_series_id": parent,
                    "published": parent_weight,
                    "reconstructed": child_sum,
                    "residual": residual,
                    "passed": abs(residual) <= tolerance,
                    "child_count": len(children),
                }
            )
    return results, skips


def price_reference_month(ref_date: date) -> date:
    """Return the ONS price reference month backing the link ending in ``ref_date``.

    ONS aggregates with a Laspeyres-type index against an annual January price
    reference and chains the series in December, so February through December
    compare against January of the same year while January itself chains on the
    preceding December.
    """
    if ref_date.month == 1:
        return date(ref_date.year - 1, 12, 1)
    return date(ref_date.year, 1, 1)


def validate_bottom_up(
    observations: dict[date, dict[str, float | None]],
    weights_by_date: dict[date, dict[str, float]],
    hierarchy: dict[str, list[str]],
    tolerance_pp: float = VALIDATION_TOLERANCE_PP,
    *,
    operational: bool = False,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Reconstruct monthly parent inflation from price-updated child weights.

    Official basket weights remain untouched in original_weights. For each parent/month
    this check price-updates the immediate-child weights to the ONS price
    reference month, normalizes them locally, takes their weighted average price
    relative, and compares it with the published parent price relative:

        phi_i(t)   = w_i(t) * I_i(t-1)/I_i(ref) /
                     sum_j w_j(t) * I_j(t-1)/I_j(ref)
        R_hat(p,t) = sum_i phi_i(t) * I_i(t)/I_i(t-1)

    Omitting the ``I_i(ref)`` term is only valid when every child shares one
    January index level. Table 38 publishes 2015=100 levels that are never
    re-referenced to January, so dropping it biases the residual within the year.

    Returns the performed checks and a count of skipped checks by reason, so a
    run that reconciles nothing cannot be mistaken for a run that reconciles
    everything.
    """
    dates = sorted(observations)
    results: list[dict[str, Any]] = []
    skips: Counter[str] = Counter()
    for previous_date, ref_date in pairwise(dates):
        if (ref_date.year * 12 + ref_date.month) - (
            previous_date.year * 12 + previous_date.month
        ) != 1:
            skips[SKIP_MISSING_OBSERVATION_MONTH] += len(hierarchy)
            continue
        current = observations[ref_date]
        previous = observations[previous_date]
        weights = weights_by_date.get(ref_date)
        if not weights:
            reason = (
                SKIP_OUTSIDE_WEIGHTS_WINDOW
                if ref_date < date(2008, 1, 1)
                else SKIP_MISSING_WEIGHT_MONTH
            )
            skips[reason] += len(hierarchy)
            continue
        reference_date = price_reference_month(ref_date)
        reference = observations.get(reference_date, {})
        for parent, children in hierarchy.items():
            parent_now = current.get(parent)
            parent_before = previous.get(parent)
            if parent_now is None or parent_before is None or parent_before == 0:
                skips[SKIP_PARENT_WITHOUT_OBSERVATION] += 1
                continue
            usable = [
                child
                for child in children
                if child in weights
                and current.get(child) is not None
                and previous.get(child) not in (None, 0)
                and (operational or reference.get(child) not in (None, 0))
            ]
            if len(usable) != len(children):
                if any(child not in weights for child in children):
                    skips[SKIP_CHILD_WITHOUT_WEIGHT] += 1
                elif not operational and any(
                    reference.get(child) in (None, 0) for child in children
                ):
                    skips[SKIP_PRICE_REFERENCE_MISSING] += 1
                else:
                    skips[SKIP_CHILD_WITHOUT_OBSERVATION] += 1
                continue
            updated = {
                child: (
                    weights[child]
                    if operational
                    else weights[child] * _observed(previous, child) / _observed(reference, child)
                )
                for child in usable
            }
            weight_total = sum(updated.values())
            if weight_total == 0:
                skips[SKIP_ZERO_WEIGHT_TOTAL] += 1
                continue
            reconstructed_relative = sum(
                updated[child] * _observed(current, child) / _observed(previous, child)
                for child in usable
            ) / (1.0 if operational else weight_total)
            published_relative = float(parent_now) / float(parent_before)
            residual_pp = (reconstructed_relative - published_relative) * 100.0
            results.append(
                {
                    "check": "bottom_up_monthly_rate",
                    "reference_date": ref_date,
                    "price_reference_date": reference_date,
                    "parent_series_id": parent,
                    "published": (published_relative - 1.0) * 100.0,
                    "reconstructed": (reconstructed_relative - 1.0) * 100.0,
                    "residual": residual_pp,
                    "passed": abs(residual_pp) <= tolerance_pp,
                    "child_count": len(usable),
                }
            )
    return results, skips


def derive_operational_weights(
    observations: dict[date, dict[str, float | None]],
    basket: dict[date, dict[str, float]],
    hierarchy: dict[str, list[str]],
) -> dict[date, dict[str, float]]:
    """Price-update W1 to local shares; never invent missing reference levels.

    The returned weights multiply month-on-month price relatives directly.
    Official W1 points per thousand belong exclusively in original_weights.
    """
    result: dict[date, dict[str, float]] = {}
    children_set = {child for children in hierarchy.values() for child in children}
    for month, official in sorted(basket.items()):
        ordinal = month.year * 12 + month.month - 2
        previous = observations.get(date(ordinal // 12, ordinal % 12 + 1, 1), {})
        reference = observations.get(price_reference_month(month), {})
        if month not in observations:
            continue
        derived: dict[str, float] = {
            series_id: 1.0
            for series_id, value in observations[month].items()
            if series_id not in children_set and value is not None and previous.get(series_id)
        }
        for parent, children in hierarchy.items():
            if not all(
                child in official
                and previous.get(child) not in (None, 0)
                and reference.get(child) not in (None, 0)
                for child in children
            ):
                continue
            updated = {
                child: official[child] * _observed(previous, child) / _observed(reference, child)
                for child in children
            }
            if any(not math.isfinite(value) or value < 0 for value in updated.values()):
                raise ValueError(f"Invalid operational weights at {month} for {parent}")
            total = sum(updated.values())
            if total <= 0:
                continue
            derived.update({child: value / total for child, value in updated.items()})
            if parent not in children_set:
                derived[parent] = 1.0
        if derived:
            result[month] = derived
    return result


def _previous_month(month: date) -> date:
    """Return the calendar month before ``month``."""
    ordinal = month.year * 12 + month.month - 2
    return date(ordinal // 12, ordinal % 12 + 1, 1)


def validate_segment_weight_sums(
    segment_weights: dict[date, dict[str, float]],
    basket: dict[date, dict[str, float]],
    hierarchy: dict[date, dict[str, list[str]]],
    tolerance: float = 0.01,
    minimum_ratio: float = MIN_SEGMENT_WEIGHT_RATIO,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Check published segment weights against the published parent basket weight.

    Segments may legitimately under-cover a parent: ONS builds a few published
    aggregates, notably actual rentals for housing, partly from administrative
    sources that are not published as consumption segments. They must never
    over-cover it, and the shortfall must stay inside the configured floor.
    """
    results: list[dict[str, Any]] = []
    skips: Counter[str] = Counter()
    for month, parents in sorted(hierarchy.items()):
        weights = segment_weights.get(month, {})
        parent_basket = basket.get(month, {})
        for parent, children in parents.items():
            parent_weight = parent_basket.get(parent)
            if parent_weight is None or parent_weight <= 0:
                skips[SKIP_PARENT_WITHOUT_WEIGHT] += 1
                continue
            if any(child not in weights for child in children):
                skips[SKIP_CHILD_WITHOUT_WEIGHT] += 1
                continue
            child_sum = sum(weights[child] for child in children)
            residual = child_sum - parent_weight
            results.append(
                {
                    "check": "segment_weight_sum",
                    "reference_date": month,
                    "parent_series_id": parent,
                    "published": parent_weight,
                    "reconstructed": child_sum,
                    "residual": residual,
                    "passed": residual <= tolerance and child_sum >= parent_weight * minimum_ratio,
                    "child_count": len(children),
                }
            )
    return results, skips


def validate_segment_bottom_up(
    observations: dict[date, dict[str, float | None]],
    weights_by_date: dict[date, dict[str, float]],
    hierarchy: dict[date, dict[str, list[str]]],
    tolerance_pp: float = SEGMENT_TOLERANCE_PP,
    *,
    operational: bool = False,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Reconstruct a published parent's monthly relative from its consumption segments.

    Segment indices are re-referenced every January, so within a link both
    months share one index reference and the price update reduces to the
    previous month's level:

        phi_i(t)   = w_i(t) * I_i(t-1) / sum_j w_j(t) * I_j(t-1)
        R_hat(p,t) = sum_i phi_i(t) * I_i(t)/I_i(t-1)

    Links touching January are not defined at source and are reported as such.
    """
    results: list[dict[str, Any]] = []
    skips: Counter[str] = Counter()
    months = sorted(hierarchy)
    for previous_date, ref_date in pairwise(months):
        parents = hierarchy[ref_date]
        if _previous_month(ref_date) != previous_date:
            skips[SKIP_MISSING_OBSERVATION_MONTH] += len(parents)
            continue
        if not linkable(previous_date, ref_date):
            skips[SKIP_SEGMENT_CHAIN_LINK] += len(parents)
            continue
        weights = weights_by_date.get(ref_date)
        if not weights:
            skips[SKIP_MISSING_WEIGHT_MONTH] += len(parents)
            continue
        current = observations.get(ref_date, {})
        previous = observations.get(previous_date, {})
        for parent, children in parents.items():
            parent_now = current.get(parent)
            parent_before = previous.get(parent)
            if parent_now is None or parent_before is None or parent_before == 0:
                skips[SKIP_PARENT_WITHOUT_OBSERVATION] += 1
                continue
            if set(children) != set(hierarchy[previous_date].get(parent, [])):
                skips[SKIP_SEGMENT_COMPOSITION] += 1
                continue
            if any(child not in weights for child in children):
                skips[SKIP_CHILD_WITHOUT_WEIGHT] += 1
                continue
            if any(
                current.get(child) is None or previous.get(child) in (None, 0) for child in children
            ):
                skips[SKIP_CHILD_WITHOUT_OBSERVATION] += 1
                continue
            updated = {
                child: (
                    weights[child] if operational else weights[child] * _observed(previous, child)
                )
                for child in children
            }
            weight_total = sum(updated.values())
            if weight_total == 0:
                skips[SKIP_ZERO_WEIGHT_TOTAL] += 1
                continue
            reconstructed_relative = sum(
                updated[child] * _observed(current, child) / _observed(previous, child)
                for child in children
            ) / (1.0 if operational else weight_total)
            published_relative = float(parent_now) / float(parent_before)
            residual_pp = (reconstructed_relative - published_relative) * 100.0
            results.append(
                {
                    "check": "segment_bottom_up_monthly_rate",
                    "reference_date": ref_date,
                    "price_reference_date": previous_date,
                    "parent_series_id": parent,
                    "published": (published_relative - 1.0) * 100.0,
                    "reconstructed": (reconstructed_relative - 1.0) * 100.0,
                    "residual": residual_pp,
                    "passed": abs(residual_pp) <= tolerance_pp,
                    "child_count": len(children),
                }
            )
    return results, skips


def derive_segment_operational_weights(
    observations: dict[date, dict[str, float | None]],
    segment_weights: dict[date, dict[str, float]],
    hierarchy: dict[date, dict[str, list[str]]],
) -> dict[date, dict[str, float]]:
    """Price-update published segment weights into stored local shares.

    A share is only derived for a month whose link to the previous month exists
    at source; a January re-reference month has no defined share and none is
    invented for it.
    """
    result: dict[date, dict[str, float]] = {}
    for month, parents in sorted(hierarchy.items()):
        previous_date = _previous_month(month)
        if not linkable(previous_date, month):
            continue
        weights = segment_weights.get(month, {})
        previous = observations.get(previous_date, {})
        derived: dict[str, float] = {}
        for children in parents.values():
            if any(child not in weights or previous.get(child) in (None, 0) for child in children):
                continue
            updated = {child: weights[child] * _observed(previous, child) for child in children}
            if any(not math.isfinite(value) or value < 0 for value in updated.values()):
                raise ValueError(f"Invalid segment operational weights at {month}")
            total = sum(updated.values())
            if total <= 0:
                continue
            derived.update({child: value / total for child, value in updated.items()})
        if derived:
            result[month] = derived
    return result


def log_validation_summary(
    results: list[dict[str, Any]], skips: Counter[str], label: str
) -> tuple[int, int, int, float, float, int]:
    """Log pass/fail/skip counts and coverage.

    Returns ``(passed, failed, skipped, coverage, max_abs_residual, attempted)``.
    Coverage is the share of reconcilable checks that actually ran, so a run
    that skips everything reports 0.0 rather than a clean
    ``checks=0 passed=0 failed=0``. ``attempted`` separates "nothing could be
    reconciled at source" from "everything that could be reconciled was
    skipped", which are the same number of checks but not the same outcome.
    """
    passed = sum(bool(row["passed"]) for row in results)
    failed = len(results) - passed
    skipped = sum(skips.values())
    unreconcilable = sum(skips.get(reason, 0) for reason in UNRECONCILABLE_AT_SOURCE)
    attempted = len(results) + skipped - unreconcilable
    coverage = len(results) / attempted if attempted else 0.0
    residuals = sorted(abs(float(row["residual"])) for row in results)
    max_residual = residuals[-1] if residuals else 0.0
    median_residual = residuals[len(residuals) // 2] if residuals else 0.0
    logger.info(
        "%s validation: checks=%d passed=%d failed=%d skipped=%d coverage=%.4f "
        "median_abs_residual=%.6f max_abs_residual=%.6f",
        label,
        len(results),
        passed,
        failed,
        skipped,
        coverage,
        median_residual,
        max_residual,
    )
    for reason, count in sorted(skips.items()):
        if reason in UNRECONCILABLE_AT_SOURCE:
            logger.info(
                "%s validation: %d checks not reconcilable at source (%s)", label, count, reason
            )
        else:
            logger.warning("%s validation: %d checks skipped (%s)", label, count, reason)
    if failed:
        worst = sorted(results, key=lambda row: abs(float(row["residual"])), reverse=True)[:10]
        for row in worst:
            if not row["passed"]:
                logger.warning(
                    "%s mismatch date=%s parent=%s residual=%.6f",
                    label,
                    row["reference_date"],
                    row["parent_series_id"],
                    row["residual"],
                )
    return passed, failed, skipped, coverage, max_residual, attempted
