"""Release-monitoring behavior for the EX-CPI watch loop."""

from __future__ import annotations

import itertools
from datetime import date

import pytest

import main

LATEST = date(2026, 7, 1)
EXPECTED = date(2026, 8, 1)


def _bounded_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = itertools.count(0.0, 5.0)
    monkeypatch.setattr(main, "MAX_WAIT", 20.0)
    monkeypatch.setattr(main, "POLL_INTERVAL", 1.0)
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(main.time, "monotonic", lambda: next(ticks))


def test_unchanged_workbook_downloads_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _bounded_clock(monkeypatch)
    downloads: list[date] = []
    monkeypatch.setattr(main, "get_workbook_fingerprint", lambda: '"same"')

    def collect(start: date) -> dict[date, dict[str, float | None]]:
        downloads.append(start)
        return {LATEST: {"EXCPI_INDEX_NATIVE_DKC6": 100.0}}

    monkeypatch.setattr(main, "collect_raw_data", collect)
    assert main._wait_for_release(LATEST) is None
    assert len(downloads) == 1


def test_new_release_returns_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    _bounded_clock(monkeypatch)
    monkeypatch.setattr(main, "get_workbook_fingerprint", lambda: '"new"')
    monkeypatch.setattr(
        main,
        "collect_raw_data",
        lambda _start: {EXPECTED: {"EXCPI_INDEX_NATIVE_DKC6": 100.0}},
    )
    result = main._wait_for_release(LATEST)
    assert result is not None
    assert max(result) == EXPECTED


def _observations(newest: date) -> dict[date, dict[str, float | None]]:
    return {newest: {"EXCPI_INDEX_NATIVE_DKC6": 100.0}}


def test_changed_workbook_is_redownloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new entity tag on each poll must cost a fresh download each time."""
    _bounded_clock(monkeypatch)
    downloads: list[date] = []
    tags = itertools.count()
    monkeypatch.setattr(main, "get_workbook_fingerprint", lambda: f'"v{next(tags)}"')

    def collect(start: date) -> dict[date, dict[str, float | None]]:
        downloads.append(start)
        return _observations(LATEST)

    monkeypatch.setattr(main, "collect_raw_data", collect)

    assert main._wait_for_release(LATEST) is None
    assert len(downloads) > 1


def test_missing_entity_tag_never_optimises_a_release_away(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source exposing no validator must be re-downloaded, not assumed stale."""
    _bounded_clock(monkeypatch)
    downloads: list[date] = []
    monkeypatch.setattr(main, "get_workbook_fingerprint", lambda: None)

    def collect(start: date) -> dict[date, dict[str, float | None]]:
        downloads.append(start)
        return _observations(LATEST)

    monkeypatch.setattr(main, "collect_raw_data", collect)

    assert main._wait_for_release(LATEST) is None
    assert len(downloads) > 1


def test_timeout_without_a_release_is_a_normal_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polling past the deadline returns None so the run can exit 0 and write nothing."""
    _bounded_clock(monkeypatch)
    monkeypatch.setattr(main, "get_workbook_fingerprint", lambda: '"same"')
    monkeypatch.setattr(main, "collect_raw_data", lambda _start: _observations(LATEST))

    assert main._wait_for_release(LATEST) is None


def _forbid_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    def never(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the watch loop must not run for this invocation")

    monkeypatch.setattr(main, "_wait_for_release", never)
    monkeypatch.setattr(main.time, "sleep", never)


def _route(monkeypatch: pytest.MonkeyPatch, *, latest: date | None) -> list[date]:
    """Record the window each route asks the source for, stopping before any I/O."""
    windows: list[date] = []

    def collect(start: date) -> dict[date, dict[str, float | None]]:
        windows.append(start)
        raise _StopRouting

    monkeypatch.setattr(main, "init_db", lambda _engine: None)
    monkeypatch.setattr(main, "earliest_legacy_weight_month", lambda _engine: None)
    monkeypatch.setattr(main, "get_max_reference_date", lambda _engine: latest)
    monkeypatch.setattr(main, "collect_raw_data", collect)
    return windows


class _StopRouting(Exception):
    """Raised once the route has chosen its window; nothing after it is under test."""


def test_an_empty_database_builds_history_without_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first deployment must populate history now, not wait out a release window."""
    windows = _route(monkeypatch, latest=None)
    _forbid_polling(monkeypatch)

    with pytest.raises(_StopRouting):
        main._collect(main._parse_args([]), object())  # type: ignore[arg-type]

    assert windows == [main._shift_months(main.DEFAULT_START_DATE, -12)]


def test_an_explicit_start_date_never_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--start-date`` is a bounded backfill, not a reason to watch."""
    windows = _route(monkeypatch, latest=LATEST)
    _forbid_polling(monkeypatch)

    with pytest.raises(_StopRouting):
        main._collect(main._parse_args(["--start-date", "2020-03-01"]), object())  # type: ignore[arg-type]

    assert windows == [date(2019, 3, 1)]


def test_no_watch_on_a_populated_database_rewinds_without_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows = _route(monkeypatch, latest=LATEST)
    _forbid_polling(monkeypatch)

    with pytest.raises(_StopRouting):
        main._collect(main._parse_args(["--no-watch"]), object())  # type: ignore[arg-type]

    assert windows == [main._shift_months(main._rewind_start(LATEST), -12)]


def test_a_populated_database_without_no_watch_enters_the_watch_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once history exists, a bare run waits for the next expected month."""
    _route(monkeypatch, latest=LATEST)
    watched: list[date] = []

    def fake_wait(latest: date) -> None:
        watched.append(latest)

    monkeypatch.setattr(main, "_wait_for_release", fake_wait)

    assert main._collect(main._parse_args([]), object()) == 0  # type: ignore[arg-type]
    assert watched == [LATEST]
