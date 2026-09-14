"""Standalone ONS collector for ten UK CPI exclusion aggregates."""

from __future__ import annotations

import argparse
import io
import logging
import sys
import time
import traceback
from datetime import UTC, date, datetime

from sqlalchemy.engine import Engine

from scripts.config import (
    DEFAULT_START_DATE, LOG_LEVEL, MAX_WAIT, POLL_INTERVAL,
    START_DATE_LOOKBACK_MONTHS, missing_environment, unresolved_credentials,
)
from scripts.db import build_engine
from scripts.extract import collect_raw_data, get_last_publish_date, get_series_catalog, get_workbook_fingerprint
from scripts.init_db import init_db
from scripts.metadata import assert_current_series_ids, upsert_metadata
from scripts.original_weights import upsert_original_weights
from scripts.run_logs import insert_run_log
from scripts.special_aggregate_rates import published_12m_rate_checks
from scripts.special_aggregate_vintages import (
    DOUBLE_WEIGHT_START_YEAR,
    build_exclusion_weight_regimes,
    build_mm23_original_weight_layer,
    collect_january_weight_panels,
    discover_mm23_snapshots,
    january_regime_snapshots,
)
from scripts.special_aggregates import collect_mm23_special_aggregates, complement_weight_checks
from scripts.time_series import get_max_reference_date, upsert_time_series

logger = logging.getLogger("main")


def _setup_logging(level: str) -> io.StringIO:
    buffer = io.StringIO()
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for target in (logging.StreamHandler(sys.stdout), logging.StreamHandler(buffer)):
        target.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.ERROR)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    capture = logging.StreamHandler(buffer)
    capture.setFormatter(formatter)
    root.addHandler(stream)
    root.addHandler(capture)
    logging.getLogger("main").setLevel(level.upper())
    logging.getLogger("scripts").setLevel(level.upper())
    return buffer


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect ONS UK CPI exclusion aggregates.")
    parser.add_argument("--log-level", default=LOG_LEVEL)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--no-watch", action="store_true")
    return parser.parse_args(argv)


def _shift_months(value: date, months: int) -> date:
    ordinal = value.year * 12 + value.month - 1 + months
    return date(ordinal // 12, ordinal % 12 + 1, 1)


def _rewind_start(latest: date) -> date:
    return _shift_months(latest.replace(day=1), -START_DATE_LOOKBACK_MONTHS)


def _wait_for_release(latest: date) -> dict[date, dict[str, float | None]] | None:
    expected = _shift_months(latest.replace(day=1), 1)
    start = _shift_months(expected, -12)
    deadline = time.monotonic() + MAX_WAIT
    fingerprint: str | None = None
    downloaded = False
    while True:
        current = get_workbook_fingerprint()
        if not downloaded or current is None or current != fingerprint:
            parsed = collect_raw_data(start)
            fingerprint = current
            downloaded = True
            if max(parsed) >= expected:
                return parsed
        if time.monotonic() >= deadline:
            logger.info("No EX-CPI release for %s before timeout", expected)
            return None
        time.sleep(min(POLL_INTERVAL, max(0.0, deadline - time.monotonic())))


def _preflight() -> None:
    missing = missing_environment()
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
    deferred = unresolved_credentials()
    if deferred:
        logger.warning("Deferred Databricks credentials: %s", ", ".join(deferred))


def _validate(checks: list[dict[str, object]], label: str) -> None:
    if not checks:
        raise ValueError(f"{label}: no checks executed")
    failures = [check for check in checks if not bool(check["passed"])]
    residual_key = "residual_pp" if "residual_pp" in checks[0] else "residual"
    residuals = sorted(abs(float(check[residual_key])) for check in checks)
    median = residuals[len(residuals) // 2]
    logger.info(
        "%s checks=%d failures=%d max_residual=%.6f median_residual=%.6f",
        label, len(checks), len(failures), max(residuals), median,
    )
    if failures:
        raise ValueError(f"{label}: {len(failures)} checks outside tolerance")


def _weight_rows(
    regimes: dict[date, dict[str, float]],
    catalog: dict[str, dict[str, str]],
) -> list[dict[str, object]]:
    originals, _audit = build_mm23_original_weight_layer(regimes, catalog)
    return [
        {
            "series_id": series_id,
            "reference_date": month,
            "weight": weight,
            "weight_base_year": month.year,
        }
        for month, values in sorted(originals.items())
        for series_id, weight in sorted(values.items())
    ]


def _collect(args: argparse.Namespace, engine: Engine) -> int:
    init_db(engine)
    latest = get_max_reference_date(engine)
    if args.start_date:
        start = args.start_date.replace(day=1)
        observations = collect_raw_data(_shift_months(start, -12))
    elif latest is None:
        start = DEFAULT_START_DATE
        observations = collect_raw_data(_shift_months(start, -12))
    elif args.no_watch:
        start = _rewind_start(latest)
        observations = collect_raw_data(_shift_months(start, -12))
    else:
        observations = _wait_for_release(latest)
        if observations is None:
            return 0
        start = min(observations)

    panel = collect_mm23_special_aggregates()
    _validate(complement_weight_checks(panel), "MM23 complement weights")
    _validate(
        published_12m_rate_checks(observations, get_series_catalog(), panel),
        "MM23 published 12-month rates",
    )

    release_date = get_last_publish_date() or date.today()
    snapshot_map = january_regime_snapshots(discover_mm23_snapshots())
    first_year = max(DOUBLE_WEIGHT_START_YEAR, start.year)
    january_panels = collect_january_weight_panels(
        snapshot_map, start_year=first_year, end_year=release_date.year - 1
    ) if release_date.year - 1 >= first_year else {}
    regimes = build_exclusion_weight_regimes(
        panel, release_date, january_panels, start_year=start.year
    )
    rows = _weight_rows(regimes, get_series_catalog())

    collected_at = datetime.now(UTC)
    with engine.begin() as conn:
        assert_current_series_ids(conn)
        new_obs, new_vintages = upsert_time_series(conn, observations, collected_at)
        new_weights, weight_vintages = upsert_original_weights(conn, rows, collected_at)
        metadata_inserted, metadata_updated = upsert_metadata(
            conn, observations, collected_at, get_series_catalog()
        )
    logger.info(
        "Run result: observations=%d vintages=%d official_weights=%d "
        "weight_vintages=%d metadata_inserted=%d metadata_updated=%d",
        new_obs, new_vintages, new_weights, weight_vintages,
        metadata_inserted, metadata_updated,
    )
    return 0


def main(args: argparse.Namespace) -> int:
    logger.info("Starting standalone ONS UK EX-CPI collector")
    _preflight()
    engine = build_engine()
    try:
        return _collect(args, engine)
    finally:
        engine.dispose()


def run(argv: list[str] | None = None) -> int:
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
                log_engine, started_at, finished_at, status,
                log_buffer.getvalue(), traceback_text,
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
