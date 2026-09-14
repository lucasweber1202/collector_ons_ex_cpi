"""Engine construction must not leak credentials, and a run log must always land."""

from __future__ import annotations

import logging
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from scripts import config, db, run_logs

MOMENT = datetime(2026, 8, 19, 6, 0)  # noqa: DTZ001


def test_local_engine_logs_a_redacted_url(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A database password must never reach the log buffer that is persisted."""
    monkeypatch.setattr(db, "PROD", False)
    monkeypatch.setattr(db, "DATABASE_URL", "postgresql+psycopg2://user:s3cr3t@host:5432/macro")

    with caplog.at_level(logging.INFO, logger="scripts.db"):
        engine = db.build_engine()
    try:
        logged = " ".join(record.getMessage() for record in caplog.records)
        assert "s3cr3t" not in logged
        assert "***" in logged
        assert "host:5432/macro" in logged
    finally:
        engine.dispose()


def test_local_engine_requires_a_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "PROD", False)
    monkeypatch.setattr(db, "DATABASE_URL", "")

    with pytest.raises(RuntimeError, match="COLLECTOR_DB_URL is required"):
        db.build_engine()


def test_run_log_is_written_for_both_outcomes(engine: Engine) -> None:
    run_logs.insert_run_log(engine, MOMENT, MOMENT, "success", "ok", None)
    run_logs.insert_run_log(engine, MOMENT, MOMENT, "error", "boom", "Traceback...")

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT status, log_text, traceback FROM collector_ons_ex_cpi.logs ORDER BY id")
        ).all()
    assert [row[0] for row in rows] == ["success", "error"]
    assert rows[1][2] == "Traceback..."


def test_oversized_text_is_truncated_visibly(engine: Engine) -> None:
    """A log longer than the column must be cut with the marker, not rejected."""
    run_logs.insert_run_log(engine, MOMENT, MOMENT, "success", "x" * 200_000, "y" * 200_000)

    with engine.connect() as conn:
        log_text, traceback_text = conn.execute(
            text("SELECT log_text, traceback FROM collector_ons_ex_cpi.logs")
        ).one()
    assert len(log_text) == run_logs._MAX_TEXT
    assert log_text.endswith("[..., truncated ...]")
    assert len(traceback_text) == run_logs._MAX_TEXT
    assert traceback_text.endswith("[..., truncated ...]")


def test_text_at_the_limit_is_left_alone() -> None:
    exact = "z" * run_logs._MAX_TEXT
    assert run_logs._truncate(exact) == exact
    assert run_logs._truncate(None) is None


def test_a_logging_failure_never_masks_the_pipeline_result(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """Log persistence is best-effort; it must not raise over the real error."""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE collector_ons_ex_cpi.logs"))

    with caplog.at_level(logging.ERROR, logger="scripts.run_logs"):
        run_logs.insert_run_log(engine, MOMENT, MOMENT, "error", "boom", None)

    assert any("Could not write run log" in record.getMessage() for record in caplog.records)


def test_schema_and_catalog_names_match_the_repository() -> None:
    """The fleet contract requires SCHEMA_NAME to equal the repository name."""
    assert config.SCHEMA_NAME == "collector_ons_ex_cpi"
    assert config.CATALOG_NAME == "macrobond_inhouse"
