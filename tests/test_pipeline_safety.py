"""Validation is mandatory, and a release is never left half written."""

from __future__ import annotations

from collections import Counter
from datetime import date
from unittest.mock import Mock

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

import main
from scripts.segments import SegmentPanel
from tests.conftest import catalog_entry

_PARENT = catalog_entry("COICOP", "ALL", "D7BT", "CPI ALL ITEMS", level="all_items")
_FIRST = catalog_entry(
    "COICOP", "D01", "D7BU", "First division", level="division", parent=_PARENT[0]
)
_SECOND = catalog_entry(
    "COICOP", "D02", "D7BV", "Second division", level="division", parent=_PARENT[0]
)
PARENT, FIRST, SECOND = _PARENT[0], _FIRST[0], _SECOND[0]
CATALOG = dict([_PARENT, _FIRST, _SECOND])

JANUARY, FEBRUARY = date(2024, 1, 1), date(2024, 2, 1)
OBSERVATIONS: dict[date, dict[str, float | None]] = {
    JANUARY: {PARENT: 100.0, FIRST: 100.0, SECOND: 100.0},
    FEBRUARY: {PARENT: 102.5, FIRST: 110.0, SECOND: 100.0},
}
BASKET = {FEBRUARY: {PARENT: 1000.0, FIRST: 250.0, SECOND: 750.0}}


def _wire(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> None:
    """Run the real pipeline against a real database with the source stubbed."""
    monkeypatch.setattr(main, "_preflight", lambda: None)
    monkeypatch.setattr(main, "build_engine", lambda: engine)
    monkeypatch.setattr(main, "init_db", lambda _engine: None)
    monkeypatch.setattr(main, "collect_raw_data", lambda _start: OBSERVATIONS)
    monkeypatch.setattr(main, "collect_weights", lambda _start: BASKET)
    monkeypatch.setattr(main, "get_series_catalog", lambda: CATALOG)
    monkeypatch.setattr(main, "get_original_weights", dict)
    monkeypatch.setattr(main, "get_original_weight_catalog", dict)
    monkeypatch.setattr(main, "collect_segments", lambda *_args: SegmentPanel({}, {}, {}, {}))


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            table: conn.execute(
                text(f"SELECT COUNT(*) FROM collector_ons_ex_cpi.{table}")
            ).scalar_one()
            for table in ("time_series", "weights", "original_weights", "metadata")
        }


def test_a_complete_release_is_written(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> None:
    _wire(monkeypatch, engine)
    assert main.main(main._parse_args(["--no-watch"])) == 0
    counts = _counts(engine)
    assert counts["time_series"] == 6
    assert counts["metadata"] == 3
    assert counts["weights"] == 3


def test_a_failure_after_the_observation_write_persists_nothing(
    monkeypatch: pytest.MonkeyPatch, engine: Engine
) -> None:
    """Observations, weights and metadata are one release or none of it."""
    _wire(monkeypatch, engine)
    monkeypatch.setattr(main, "upsert_metadata", Mock(side_effect=RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        main.main(main._parse_args(["--no-watch"]))
    assert _counts(engine) == {
        "time_series": 0,
        "weights": 0,
        "original_weights": 0,
        "metadata": 0,
    }


def test_a_failure_after_the_weight_write_persists_nothing(
    monkeypatch: pytest.MonkeyPatch, engine: Engine
) -> None:
    _wire(monkeypatch, engine)
    monkeypatch.setattr(main, "upsert_weights", Mock(side_effect=RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        main.main(main._parse_args(["--no-watch"]))
    assert _counts(engine)["time_series"] == 0


def test_failure_is_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = Mock()
    log = Mock()
    monkeypatch.setattr(main, "build_engine", lambda: engine)
    monkeypatch.setattr(main, "init_db", lambda _: None)
    monkeypatch.setattr(main, "insert_run_log", log)
    monkeypatch.setattr(main, "main", Mock(side_effect=ValueError("source failure")))
    assert main.run(["--no-watch"]) == 1
    assert log.call_args.args[3] == "error"
    assert "source failure" in log.call_args.args[5]
    engine.dispose.assert_called_once()


def test_validation_cannot_be_disabled(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> None:
    _wire(monkeypatch, engine)
    monkeypatch.setattr(main, "validate_weight_sums", lambda *_: ([], Counter()))
    monkeypatch.setattr(main, "validate_bottom_up", lambda *_, **_kw: ([], Counter()))
    with pytest.raises(ValueError, match="Validation failed"):
        main.main(main._parse_args(["--no-watch", "--strict-validation"]))
    assert _counts(engine)["time_series"] == 0


def test_an_empty_segment_window_does_not_fake_a_pass(
    monkeypatch: pytest.MonkeyPatch, engine: Engine
) -> None:
    """A window that should contain segments must not persist without them."""
    _wire(monkeypatch, engine)
    recent = {
        date(2026, 1, 1): {PARENT: 100.0, FIRST: 100.0, SECOND: 100.0},
        date(2026, 2, 1): {PARENT: 102.5, FIRST: 110.0, SECOND: 100.0},
    }
    monkeypatch.setattr(main, "collect_raw_data", lambda _start: recent)
    monkeypatch.setattr(
        main, "collect_weights", lambda _start: {date(2026, 2, 1): BASKET[FEBRUARY]}
    )
    with pytest.raises(ValueError, match="no published edition was collected"):
        main.main(main._parse_args(["--no-watch"]))
    assert _counts(engine)["time_series"] == 0


def test_extraction_includes_december_for_january_chain() -> None:
    assert main._anchor_to_january(date(2026, 7, 1)) == date(2025, 12, 1)


def test_both_weight_products_are_stamped_with_their_own_regime_year() -> None:
    """W1 labels a calendar year; a segment basket runs February to January."""
    january, february = date(2026, 1, 1), date(2026, 2, 1)
    rows = main._original_weight_rows(
        {january: {"CPI_W1_0": 1000.0}, february: {"CPI_W1_0": 1000.0}},
        {january: {"CPI_CS_SEG_220107": 7.691}, february: {"CPI_CS_SEG_220107": 7.775}},
    )
    stamped = {(row["series_id"], row["reference_date"]): row["weight_base_year"] for row in rows}
    assert stamped[("CPI_W1_0", january)] == 2026
    assert stamped[("CPI_CS_SEG_220107", january)] == 2025
    assert stamped[("CPI_CS_SEG_220107", february)] == 2026


def test_one_series_cannot_carry_two_official_weights_in_a_month() -> None:
    month = date(2026, 2, 1)
    with pytest.raises(ValueError, match="same series and month"):
        main._original_weight_rows({month: {"CPI_W1_0": 1.0}}, {month: {"CPI_W1_0": 2.0}})


def test_a_series_published_by_two_layers_is_refused() -> None:
    month = date(2026, 2, 1)
    with pytest.raises(ValueError, match="published by two ONS layers"):
        main._merge_observations({month: {PARENT: 100.0}}, {month: {PARENT: 101.0}})


def test_a_series_cannot_carry_two_operational_weights() -> None:
    month = date(2026, 2, 1)
    with pytest.raises(ValueError, match="two operational weights"):
        main._merge_weights({month: {PARENT: 1.0}}, {month: {PARENT: 0.5}})


def test_a_lagging_segment_dataset_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    """A CPI release can land before its consumption-segment edition."""
    parsed: dict[date, dict[str, float | None]] = {date(2026, 8, 1): {PARENT: 100.0}}
    segments = {date(2026, 7, 1): {"CPI_CS_SEG_220107": 103.0}}

    with caplog.at_level("WARNING", logger="main"):
        main._log_segment_lag(parsed, segments)

    assert any("trail the CPI release by 1 month" in r.getMessage() for r in caplog.records)


def test_an_aligned_segment_dataset_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    parsed: dict[date, dict[str, float | None]] = {date(2026, 7, 1): {PARENT: 100.0}}
    segments = {date(2026, 7, 1): {"CPI_CS_SEG_220107": 103.0}}

    with caplog.at_level("WARNING", logger="main"):
        main._log_segment_lag(parsed, segments)

    assert not caplog.records
