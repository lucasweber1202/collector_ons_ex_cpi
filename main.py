"""ONS UK CPI collector with basket weights and hierarchy-aware reconciliation."""

from __future__ import annotations

import argparse
import io
import logging
import sys
import time
import traceback
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from scripts.config import (
    DEFAULT_START_DATE,
    LOG_LEVEL,
    MAX_WAIT,
    MIN_VALIDATION_COVERAGE,
    POLL_INTERVAL,
    ROOT_DIR,
    START_DATE_LOOKBACK_MONTHS,
    missing_environment,
    unresolved_credentials,
)
from scripts.db import build_engine
from scripts.export_validation_xlsx import export_validation_xlsx
from scripts.extract import (
    collect_raw_data,
    collect_weights,
    get_original_weight_catalog,
    get_original_weights,
    get_series_catalog,
    get_workbook_fingerprint,
    register_series,
)
from scripts.init_db import init_db
from scripts.metadata import assert_current_series_ids, upsert_metadata
from scripts.original_weights import upsert_original_weights
from scripts.run_logs import insert_run_log
from scripts.segments import SEGMENT_FIRST_MONTH, SegmentPanel, chain_year, collect_segments
from scripts.time_series import get_max_reference_date, upsert_time_series
from scripts.validate import (
    build_hierarchy,
    derive_operational_weights,
    derive_segment_operational_weights,
    log_validation_summary,
    validate_bottom_up,
    validate_segment_bottom_up,
    validate_segment_weight_sums,
    validate_weight_sums,
)
from scripts.weights import assert_operational_storage, upsert_weights

logger = logging.getLogger("main")

# Dialects whose multi-statement transaction covers every table a release
# touches. Databricks SQL commits each statement on its own, so there the run
# relies on the idempotent revision rewind to repair an interrupted release.
TRANSACTIONAL_DIALECTS = frozenset({"postgresql", "sqlite"})


def _setup_logging(level: str) -> io.StringIO:
    """Capture collector logs while suppressing noisy third-party INFO output."""
    buffer = io.StringIO()
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setFormatter(formatter)
    buffer_handler = logging.StreamHandler(stream=buffer)
    buffer_handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.ERROR)
    root.addHandler(stream_handler)
    root.addHandler(buffer_handler)
    app_level = level.upper()
    logging.getLogger("main").setLevel(app_level)
    logging.getLogger("scripts").setLevel(app_level)
    return buffer


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect official ONS UK CPI indices, basket weights, and bottom-up checks."
    )
    parser.add_argument("--log-level", default=LOG_LEVEL)
    parser.add_argument("--start-date", type=date.fromisoformat, default=None)
    parser.add_argument(
        "--no-watch",
        action="store_true",
        help="Run once without waiting for the next monthly release.",
    )
    parser.add_argument(
        "--strict-validation",
        action="store_true",
        help=("Compatibility flag: validation is now mandatory on every run."),
    )
    parser.add_argument(
        "--export-validation",
        action="store_true",
        help="Write _verify_xls/ons_cpi_validation.xlsx.",
    )
    return parser.parse_args(argv)


def _shift_months(value: date, months: int) -> date:
    """Shift a first-of-month date without an extra date dependency."""
    ordinal = value.year * 12 + value.month - 1 + months
    return date(ordinal // 12, ordinal % 12 + 1, 1)


def _anchor_to_january(value: date) -> date:
    """Include preceding December and January for annual chain reconciliation.

    Bottom-up reconciliation price-updates child weights to January of the
    reconciled year, so a window that starts after January would carry no price
    reference and silently reconcile nothing.
    """
    return date(value.year - 1, 12, 1)


def _wait_for_release(latest: date) -> tuple[dict[date, dict[str, float | None]], date] | None:
    """Poll until the month after ``latest`` appears or timeout expires."""
    expected = _shift_months(latest.replace(day=1), 1)
    validation_start = _anchor_to_january(_shift_months(expected, -1))
    deadline = time.monotonic() + MAX_WAIT
    fingerprint: str | None = None
    downloaded = False
    while True:
        current = get_workbook_fingerprint()
        # Re-download only when the workbook actually changed. A source that
        # publishes no validator reports None and is always re-downloaded, so a
        # release is never missed to save a request.
        if not downloaded or current is None or current != fingerprint:
            parsed = collect_raw_data(validation_start)
            fingerprint = current
            downloaded = True
            if parsed and max(parsed) >= expected:
                logger.info("Detected ONS CPI release for %s", expected)
                return parsed, validation_start
        else:
            logger.info("CPI workbook unchanged since the last check")
        if time.monotonic() >= deadline:
            logger.info("No CPI release for %s before timeout; exiting normally", expected)
            return None
        remaining = max(0.0, deadline - time.monotonic())
        delay = min(POLL_INTERVAL, remaining)
        logger.info("CPI %s not available; checking again in %.0fs", expected, delay)
        time.sleep(delay)


def _rewind_start(latest: date) -> date:
    """Return the standard revision lookback month, anchored to its January."""
    return _anchor_to_january(_shift_months(latest.replace(day=1), -START_DATE_LOOKBACK_MONTHS))


def _preflight() -> None:
    """Fail before any database or HTTP work if the environment is incomplete."""
    missing = missing_environment()
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
    deferred = unresolved_credentials()
    if deferred:
        logger.warning(
            "No %s set; the Databricks token must come from the notebook/job context",
            " or ".join(deferred),
        )
    logger.info("Environment preflight passed")


def main(args: argparse.Namespace) -> int:
    """Run extraction, validation, weights, time series, then metadata."""
    logger.info("Starting ONS UK CPI collector")
    _preflight()
    engine = build_engine()
    try:
        return _collect(args, engine)
    finally:
        engine.dispose()


def _gate(
    label: str,
    checks: list[dict[str, Any]],
    skips: Counter[str],
    problems: list[str],
    *,
    required: bool = True,
) -> None:
    """Log one validation layer and record why it would stop persistence."""
    _, failed, _, coverage, _, attempted = log_validation_summary(checks, skips, label)
    if failed:
        problems.append(f"{label}: {failed} checks outside tolerance")
    if attempted == 0:
        if required:
            problems.append(f"{label}: nothing was reconcilable in this window")
        else:
            logger.info("%s validation: no link is defined at source in this window", label)
        return
    if not checks:
        problems.append(f"{label}: reconciled nothing")
    elif coverage < MIN_VALIDATION_COVERAGE:
        problems.append(f"{label}: coverage {coverage:.4f} below {MIN_VALIDATION_COVERAGE:.4f}")


def _log_segment_lag(
    parsed: dict[date, dict[str, float | None]], segments: dict[date, dict[str, float]]
) -> None:
    """Report how far the segment dataset trails the CPI release, if at all.

    The two products are published on their own schedules, so a CPI release can
    arrive before its consumption-segment edition. That is a normal, recoverable
    state -- the next run's revision rewind collects the missing month -- but it
    is worth saying out loud rather than leaving an operator to compare two
    other log lines.
    """
    if not parsed or not segments:
        return
    lag = (max(parsed).year * 12 + max(parsed).month) - (
        max(segments).year * 12 + max(segments).month
    )
    if lag > 0:
        logger.warning(
            "Consumption segments trail the CPI release by %d month(s): Table 38 to %s, "
            "segments to %s; the next run's rewind collects the gap",
            lag,
            max(parsed),
            max(segments),
        )


def _collect_segment_panel(start_date: date, catalog: dict[str, dict[str, str]]) -> SegmentPanel:
    """Collect consumption segments against the official weight classification codes."""
    weight_codes = [fields["code"] for fields in get_original_weight_catalog().values()]
    return collect_segments(start_date, catalog, weight_codes)


def _merge_observations(
    parsed: dict[date, dict[str, float | None]], segments: dict[date, dict[str, float]]
) -> dict[date, dict[str, float | None]]:
    """Combine the two published layers into one observation set for persistence."""
    merged: dict[date, dict[str, float | None]] = {
        month: dict(values) for month, values in parsed.items()
    }
    for month, values in segments.items():
        target = merged.setdefault(month, {})
        for series_id, value in values.items():
            if series_id in target:
                raise ValueError(f"{series_id} is published by two ONS layers at {month}")
            target[series_id] = value
    return merged


def _merge_weights(
    coicop: dict[date, dict[str, float]], segments: dict[date, dict[str, float]]
) -> dict[date, dict[str, float]]:
    """Combine operational shares from both layers, refusing any collision."""
    merged: dict[date, dict[str, float]] = {month: dict(values) for month, values in coicop.items()}
    for month, values in segments.items():
        target = merged.setdefault(month, {})
        for series_id, value in values.items():
            if series_id in target:
                raise ValueError(f"{series_id} has two operational weights at {month}")
            target[series_id] = value
    return merged


def _original_weight_rows(
    basket: dict[date, dict[str, float]], segments: dict[date, dict[str, float]]
) -> list[dict[str, Any]]:
    """Flatten both official weight products, each stamped with its own regime year.

    W1 publishes a January column and a February-December column inside one
    calendar year, so its regime year is the reference month's year. A
    consumption-segment basket instead runs February to the following January,
    so a January segment weight belongs to the previous year's basket.
    """
    rows: list[dict[str, Any]] = []
    for month, weights in sorted(basket.items()):
        rows.extend(
            {
                "series_id": series_id,
                "reference_date": month,
                "weight": weight,
                "weight_base_year": month.year,
            }
            for series_id, weight in weights.items()
        )
    for month, weights in sorted(segments.items()):
        rows.extend(
            {
                "series_id": series_id,
                "reference_date": month,
                "weight": weight,
                "weight_base_year": chain_year(month),
            }
            for series_id, weight in weights.items()
        )
    keys = {(row["series_id"], row["reference_date"]) for row in rows}
    if len(keys) != len(rows):
        raise ValueError("Two official weight products published the same series and month")
    return rows


def _collect(args: argparse.Namespace, engine: Engine) -> int:
    """Collect with a caller-owned database engine."""
    init_db(engine)
    latest = get_max_reference_date(engine)

    if args.start_date is not None:
        extraction_start = _anchor_to_january(args.start_date.replace(day=1))
        parsed = collect_raw_data(extraction_start)
    elif latest is None:
        extraction_start = DEFAULT_START_DATE
        parsed = collect_raw_data(extraction_start)
    elif args.no_watch:
        extraction_start = _rewind_start(latest)
        parsed = collect_raw_data(extraction_start)
    else:
        release = _wait_for_release(latest)
        if release is None:
            return 0
        parsed, extraction_start = release

    basket = collect_weights(max(extraction_start, date(2008, 1, 1)))
    hierarchy = build_hierarchy(get_series_catalog())
    segments = _collect_segment_panel(extraction_start, get_series_catalog())
    for series_id, fields in segments.catalog.items():
        register_series(series_id, fields)
    observations = _merge_observations(parsed, segments.observations)

    problems: list[str] = []
    weight_checks, weight_skips = validate_weight_sums(basket, hierarchy)
    _gate("Basket-weight", weight_checks, weight_skips, problems)
    bottom_up_checks, bottom_up_skips = validate_bottom_up(parsed, basket, hierarchy)
    _gate("Bottom-up", bottom_up_checks, bottom_up_skips, problems)

    segments_expected = max(observations) >= SEGMENT_FIRST_MONTH if observations else False
    if segments_expected and not segments.observations:
        problems.append("consumption segments: no published edition was collected in this window")
    _log_segment_lag(parsed, segments.observations)
    segment_weight_checks, segment_weight_skips = validate_segment_weight_sums(
        segments.official_weights, basket, segments.hierarchy
    )
    _gate(
        "Segment-weight",
        segment_weight_checks,
        segment_weight_skips,
        problems,
        required=segments_expected,
    )
    segment_checks, segment_skips = validate_segment_bottom_up(
        observations, segments.official_weights, segments.hierarchy
    )
    _gate("Segment bottom-up", segment_checks, segment_skips, problems, required=False)
    if problems:
        raise ValueError("Validation failed: " + "; ".join(problems))

    operational = _merge_weights(
        derive_operational_weights(parsed, basket, hierarchy),
        derive_segment_operational_weights(
            observations, segments.official_weights, segments.hierarchy
        ),
    )
    operational_checks, operational_skips = validate_bottom_up(
        parsed, operational, hierarchy, operational=True
    )
    _gate("Operational bottom-up", operational_checks, operational_skips, problems)
    segment_operational_checks, segment_operational_skips = validate_segment_bottom_up(
        observations, operational, segments.hierarchy, operational=True
    )
    _gate(
        "Segment operational bottom-up",
        segment_operational_checks,
        segment_operational_skips,
        problems,
        required=False,
    )
    if problems:
        raise ValueError("Validation failed: " + "; ".join(problems))

    original_weights = _original_weight_rows(get_original_weights(), segments.official_weights)

    collected_at = datetime.now(UTC)
    if engine.dialect.name not in TRANSACTIONAL_DIALECTS:
        logger.warning(
            "%s commits each statement separately; an interrupted release is repaired by the "
            "next run's revision rewind rather than rolled back",
            engine.dialect.name,
        )
    with engine.begin() as conn:
        assert_operational_storage(conn)
        assert_current_series_ids(conn)
        new_obs, new_vintages = upsert_time_series(conn, observations, collected_at)
        new_original, original_vintages = upsert_original_weights(
            conn, original_weights, collected_at
        )
        new_weights, weight_vintages = upsert_weights(conn, operational, collected_at)
        metadata_inserted, metadata_updated = upsert_metadata(
            conn, observations, collected_at, get_series_catalog()
        )
    logger.info(
        "Run result: observations=%d vintages=%d weights=%d weight_vintages=%d "
        "original_weights=%d original_weight_vintages=%d "
        "metadata_inserted=%d metadata_updated=%d",
        new_obs,
        new_vintages,
        new_weights,
        weight_vintages,
        new_original,
        original_vintages,
        metadata_inserted,
        metadata_updated,
    )
    if args.export_validation:
        output = export_validation_xlsx(
            engine,
            get_series_catalog(),
            get_original_weight_catalog(),
            weight_checks
            + bottom_up_checks
            + operational_checks
            + segment_weight_checks
            + segment_checks
            + segment_operational_checks,
            Path(ROOT_DIR) / "_verify_xls" / "ons_cpi_validation.xlsx",
            as_of=collected_at.date(),
        )
        logger.info("Wrote validation workbook to %s", output)
    return 0


def run(argv: list[str] | None = None) -> int:
    """Run the pipeline and always attempt a separate durable execution log."""
    arguments = _parse_args(argv)
    log_buffer = _setup_logging(arguments.log_level)
    started_at = datetime.now(UTC)
    status = "success"
    traceback_text: str | None = None
    return_code = 0
    try:
        return_code = main(arguments)
    except Exception:
        status = "error"
        traceback_text = traceback.format_exc()
        logger.exception("Pipeline failed")
        return_code = 1
    finally:
        finished_at = datetime.now(UTC)
        log_engine = None
        try:
            log_engine = build_engine()
            init_db(log_engine)
            insert_run_log(
                log_engine,
                started_at,
                finished_at,
                status,
                log_buffer.getvalue(),
                traceback_text,
            )
        except Exception:
            logger.exception("Could not persist run log")
            return_code = 1
        finally:
            if log_engine is not None:
                log_engine.dispose()
    return return_code


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
