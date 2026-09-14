"""Cross-source rate checks for reviewed UK CPI exclusion aggregates.

Table 38 remains the stored source of monthly index levels. MM23 publishes a
related 12-month rate for each reviewed exclusion aggregate. This module compares
the two without storing a duplicate rate series.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from scripts.special_aggregates import (
    EX_CPI_SPECIAL_AGGREGATES,
    MM23SpecialPanel,
    resolve_table38_alt_series,
)

MM23_RATE_TOLERANCE_PP = 0.10


def published_12m_rate_checks(
    table38: Mapping[date, Mapping[str, float | None]],
    catalog: Mapping[str, Mapping[str, str]],
    panel: MM23SpecialPanel,
    tolerance_pp: float = MM23_RATE_TOLERANCE_PP,
    *,
    latest_only: bool = False,
) -> list[dict[str, object]]:
    """Compare Table 38 index growth with MM23's published 12-month rates.

    MM23 rates are published to one decimal place, while Table 38 carries the
    analytical index levels to three decimals. Residuals are therefore measured
    in percentage points and checked against a 0.10 pp source-rounding margin.
    """
    resolved, missing = resolve_table38_alt_series(catalog)
    if missing:
        raise ValueError(f"Cannot validate MM23 rates; Table 38 ALT CDIDs missing: {missing}")

    checks: list[dict[str, object]] = []
    for aggregate in EX_CPI_SPECIAL_AGGREGATES:
        series_id = resolved[aggregate["index_cdid"]]
        candidates: list[tuple[date, dict[str, object]]] = []
        for month, rates in panel.monthly_rates_12m.items():
            published = rates.get(aggregate["rate_12m_cdid"])
            current = table38.get(month, {}).get(series_id)
            previous_month = date(month.year - 1, month.month, 1)
            previous = table38.get(previous_month, {}).get(series_id)
            if published is None or current is None or previous is None or previous == 0:
                continue
            calculated = 100.0 * (float(current) / float(previous) - 1.0)
            residual = calculated - published
            check: dict[str, object] = {
                "label": aggregate["label"],
                "series_id": series_id,
                "rate_cdid": aggregate["rate_12m_cdid"],
                "month": month,
                "calculated_rate": calculated,
                "published_rate": published,
                "residual_pp": residual,
                "passed": abs(residual) <= tolerance_pp,
            }
            candidates.append((month, check))
        if latest_only and candidates:
            checks.append(max(candidates, key=lambda item: item[0])[1])
        else:
            checks.extend(check for _, check in sorted(candidates, key=lambda item: item[0]))
    return checks
