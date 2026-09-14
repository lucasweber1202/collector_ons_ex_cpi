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
