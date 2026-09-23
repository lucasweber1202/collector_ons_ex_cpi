"""Operational shares for EX-CPI, and the check that they rebuild the index.

`original_weights` answers "what did ONS publish": the annual MM23 basket for
each exclusion aggregate and its complement, in parts per thousand, carrying
its regime year. That is the source record and it is stored verbatim.

It is not, on its own, a reconstruction system. Applied directly to index
levels,

    I_all(t) =? ( w_ex * I_ex(t) + w_c * I_c(t) ) / 1000

leaves residuals up to 1.45 index points against live MM23, and those
residuals scale with the size of the complement -- the signature of a wrong
model, not of rounding.

The form that does close is the December-linked Young index, in which shares
apply to price ratios rebased on the previous December:

    I_all(t) = I_all(Dec) * ( s_ex * I_ex(t)/I_ex(Dec)
                            + s_c  * I_c (t)/I_c (Dec) )

    s_ex = w_ex / (w_ex + w_c),   s_c = w_c / (w_ex + w_c),   s_ex + s_c = 1

Measured the same way, that lands at a maximum residual of 0.18 index points,
uniform across all ten aggregates. MM23 publishes indices to one decimal, so
combining three rounded inputs implies a floor of roughly that size: the
remaining error is the file's own precision, not the model's.

Those shares are the operational system. They live in `weights`, always in
[0, 1], and they are what an analyst needs to rebuild a target from stored
outputs alone.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from scripts.special_aggregates import (
    EX_CPI_SPECIAL_AGGREGATES,
    HEADLINE_INDEX_CDID,
    MM23SpecialPanel,
)

# Measured over all 3,680 (aggregate, month) reconstructions, 1996-2026:
#
#   median 0.0413   p99 0.1439   p99.5 0.1547   p99.9 0.1644   max 0.1832
#
# The residual is uniform rather than structured -- every one of the ten
# aggregates maxes between 0.1439 and 0.1832, and every decade between 0.1591
# and 0.1832. Uniformity across both cuts is the signature of a rounding floor,
# not of model error: MM23 publishes indices to one decimal, and this
# reconstruction combines five rounded inputs before being compared against a
# sixth.
#
# 0.35 is kept, at x1.91 over the observed maximum. Tightening buys nothing
# real: the regression this gate exists to catch is level aggregation, which
# errs at 1.4492 -- eight times this tolerance -- so 0.20 and 0.35 detect it
# equally, while 0.20 sits only x1.09 over a maximum that is itself set by the
# source's precision. That trades no extra detection for real false-failure
# risk on a future month with worse rounding luck.
#
# tests/test_reconciliation.py bounds this on both sides, so it can be neither
# inflated to hide a regression nor tightened into flakiness.
RECONSTRUCTION_TOLERANCE = 0.35

# The worst residual observed over the full published history, and the error
# the discredited level-aggregation form produces. The tolerance must sit
# strictly between them.
OBSERVED_MAX_RESIDUAL = 0.1832
LEVEL_AGGREGATION_MAX_RESIDUAL = 1.4492

# A pair of shares must still sum to one after normalisation.
SHARE_SUM_TOLERANCE = 1e-9


def operational_share_id(weight_cdid: str) -> str:
    """Return the stored identity of the share derived from one MM23 weight."""
    native = re.sub(r"[^A-Z0-9]+", "", weight_cdid.strip().upper())
    if not native:
        raise ValueError("MM23 weight CDID is empty")
    return f"EXCPI_SHARE_NATIVE_{native}"


def parse_operational_share_id(series_id: str) -> tuple[str, str, str, str]:
    """Split a share identity back into its parts, or raise ValueError."""
    parts = series_id.split("_")
    if len(parts) != 4 or parts[:3] != ["EXCPI", "SHARE", "NATIVE"]:
        raise ValueError(f"Invalid EX-CPI operational share series_id: {series_id}")
    if not parts[3]:
        raise ValueError(f"Empty CDID in operational share series_id: {series_id}")
    return (parts[0], parts[1], parts[2], parts[3])


def build_operational_shares(
    regimes: Mapping[date, Mapping[str, float]],
) -> list[dict[str, Any]]:
    """Normalise each aggregate/complement weight pair into shares on [0, 1].

    The pair is normalised against its own sum rather than a hardcoded 1000, so
    a month whose published pair does not total the basket still yields shares
    that sum to one and the discrepancy stays visible in `original_weights`.
    """
    rows: list[dict[str, Any]] = []
    for month, values in sorted(regimes.items()):
        for aggregate in EX_CPI_SPECIAL_AGGREGATES:
            exclusion = values.get(aggregate["weight_cdid"])
            complement = values.get(aggregate["complement_weight_cdid"])
            if exclusion is None or complement is None:
                raise ValueError(f"Missing MM23 weight pair for {aggregate['label']!r} at {month}")
            total = exclusion + complement
            if total <= 0:
                raise ValueError(
                    f"Non-positive MM23 weight pair for {aggregate['label']!r} at {month}"
                )
            for component_cdid, weight in (
                (aggregate["index_cdid"], exclusion),
                (aggregate["complement_index_cdid"], complement),
            ):
                rows.append(
                    {
                        "series_id": f"EXCPI_INDEX_NATIVE_{component_cdid}",
                        "reference_date": month,
                        "weight": weight / total,
                    }
                )
    return rows


def _december_before(month: date) -> date:
    return date(month.year - 1, 12, 1)


def reconstruction_checks(
    panel: MM23SpecialPanel,
    regimes: Mapping[date, Mapping[str, float]],
) -> list[dict[str, object]]:
    """Rebuild every aggregate from its complement and score the residual.

    Each row is one (aggregate, month) reconstruction, in the shape
    ``scripts/main._validate`` consumes.
    """
    checks: list[dict[str, object]] = []
    for aggregate in EX_CPI_SPECIAL_AGGREGATES:
        for month, weights in sorted(regimes.items()):
            december = _december_before(month)
            current = panel.monthly_indices.get(month)
            base = panel.monthly_indices.get(december)
            if current is None or base is None:
                continue
            exclusion_weight = weights.get(aggregate["weight_cdid"])
            complement_weight = weights.get(aggregate["complement_weight_cdid"])
            index_now = current.get(aggregate["index_cdid"])
            complement_now = current.get(aggregate["complement_index_cdid"])
            headline_now = current.get(HEADLINE_INDEX_CDID)
            index_base = base.get(aggregate["index_cdid"])
            complement_base = base.get(aggregate["complement_index_cdid"])
            headline_base = base.get(HEADLINE_INDEX_CDID)
            if None in (
                exclusion_weight,
                complement_weight,
                index_now,
                complement_now,
                headline_now,
                index_base,
                complement_base,
                headline_base,
            ):
                continue
            assert exclusion_weight is not None and complement_weight is not None
            assert index_now is not None and complement_now is not None
            assert headline_now is not None and headline_base is not None
            assert index_base is not None and complement_base is not None
            if 0 in (index_base, complement_base, headline_base):
                continue
            total = exclusion_weight + complement_weight
            exclusion_share = exclusion_weight / total
            complement_share = complement_weight / total
            rebuilt = headline_base * (
                exclusion_share * index_now / index_base
                + complement_share * complement_now / complement_base
            )
            residual = rebuilt - headline_now
            checks.append(
                {
                    "label": aggregate["label"],
                    "reference_date": month,
                    "series_id": aggregate["index_cdid"],
                    "complement_series_id": aggregate["complement_index_cdid"],
                    "exclusion_share": exclusion_share,
                    "complement_share": complement_share,
                    "published": headline_now,
                    "reconstructed": rebuilt,
                    "residual": residual,
                    "passed": abs(residual) <= RECONSTRUCTION_TOLERANCE,
                }
            )
    return checks
