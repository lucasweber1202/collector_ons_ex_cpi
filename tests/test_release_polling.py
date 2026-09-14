"""Regression tests for conditional release polling in the watch loop."""

from __future__ import annotations

import itertools
from datetime import date

import pytest

import main

LATEST = date(2026, 7, 1)
EXPECTED = date(2026, 8, 1)


def _bounded_clock(monkeypatch: pytest.MonkeyPatch, step: float = 5.0) -> None:
    """Advance the monotonic clock on every read so the poll loop terminates."""
    ticks = itertools.count(0.0, step)
    monkeypatch.setattr(main, "MAX_WAIT", 100.0)
    monkeypatch.setattr(main, "POLL_INTERVAL", 1.0)
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(main.time, "monotonic", lambda: next(ticks))


def _collector(monkeypatch: pytest.MonkeyPatch, newest: date) -> list[date]:
    """Record every full download the loop performs."""
    downloads: list[date] = []

    def fake_collect(start_date: date) -> dict[date, dict[str, float | None]]:
        downloads.append(start_date)
        return {newest: {"CPI_COICOP_ALL_X_ALL_ITEMS": 100.0}}

    monkeypatch.setattr(main, "collect_raw_data", fake_collect)
    return downloads


def test_unchanged_workbook_is_downloaded_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stable entity tag must cost header requests, not repeated downloads."""
    _bounded_clock(monkeypatch)
    downloads = _collector(monkeypatch, LATEST)
    monkeypatch.setattr(main, "get_workbook_fingerprint", lambda: '"unchanged"')

    assert main._wait_for_release(LATEST) is None
    assert len(downloads) == 1


def test_changed_workbook_is_redownloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new entity tag on each poll must trigger a fresh download each time."""
    _bounded_clock(monkeypatch)
    downloads = _collector(monkeypatch, LATEST)
    tags = itertools.count()
    monkeypatch.setattr(main, "get_workbook_fingerprint", lambda: f'"v{next(tags)}"')

    assert main._wait_for_release(LATEST) is None
    assert len(downloads) > 1


def test_missing_entity_tag_falls_back_to_downloading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source with no validator must never have a release optimized away."""
    _bounded_clock(monkeypatch)
    downloads = _collector(monkeypatch, LATEST)
    monkeypatch.setattr(main, "get_workbook_fingerprint", lambda: None)

    assert main._wait_for_release(LATEST) is None
    assert len(downloads) > 1


def test_release_returns_immediately_anchored_to_january(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detecting the expected month returns at once, with a January-anchored window."""
    _bounded_clock(monkeypatch)
    downloads = _collector(monkeypatch, EXPECTED)
    monkeypatch.setattr(main, "get_workbook_fingerprint", lambda: '"released"')

    release = main._wait_for_release(LATEST)

    assert release is not None
    parsed, validation_start = release
    assert max(parsed) == EXPECTED
    assert validation_start == date(2025, 12, 1)
    assert len(downloads) == 1
