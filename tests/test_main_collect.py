"""End-to-end orchestration regression for the standalone EX-CPI pipeline."""

from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

import pytest

import main


def _catalog() -> dict[str, dict[str, str]]:
    return {
        "EXCPI_INDEX_NATIVE_DKC6": {
            "family": "INDEX",
            "native_id": "DKC6",
            "name": "Core CPI",
            "dataset": "ONS Table 38",
            "level": "special_aggregate",
            "provenance": "Official ONS Table 38",
            "source_url": "https://www.ons.gov.uk/",
        }
    }


def test_collect_persists_only_requested_window_and_completes_transaction(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lookback drives validation only; the requested month is the stored product."""
    lookback = date(2025, 1, 1)
    requested = date(2026, 1, 1)
    observations = {
        lookback: {"EXCPI_INDEX_NATIVE_DKC6": 100.0},
        requested: {"EXCPI_INDEX_NATIVE_DKC6": 102.5},
    }
    calls: list[str] = []

    monkeypatch.setattr(main, "init_db", lambda _engine: calls.append("init_db"))
    monkeypatch.setattr(main, "get_max_reference_date", lambda _engine: None)
    monkeypatch.setattr(main, "DEFAULT_START_DATE", requested)
    monkeypatch.setattr(
        main,
        "collect_raw_data",
        lambda start: calls.append(f"table38:{start.isoformat()}") or observations,
    )
    monkeypatch.setattr(main, "collect_mm23_special_aggregates", lambda: object())
    monkeypatch.setattr(
        main,
        "complement_weight_checks",
        lambda _panel: calls.append("mm23") or [{"passed": True, "residual": 0.0}],
    )
    monkeypatch.setattr(
        main,
        "published_12m_rate_checks",
        lambda panel, catalog, mm23: calls.append("rates")
        or [{"passed": True, "residual_pp": 0.0}],
    )
    monkeypatch.setattr(main, "get_series_catalog", _catalog)
    monkeypatch.setattr(main, "get_last_publish_date", lambda: date(2026, 2, 18))
    monkeypatch.setattr(main, "discover_mm23_snapshots", lambda: calls.append("snapshots") or [])
    monkeypatch.setattr(main, "january_regime_snapshots", lambda _snapshots: {})
    monkeypatch.setattr(main, "collect_january_weight_panels", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        main,
        "build_exclusion_weight_regimes", lambda *args, **kwargs: {requested: {}}
    )
    monkeypatch.setattr(
        main,
        "_weight_rows",
        lambda _regimes, _series_catalog: [
            {
                "series_id": "EXCPI_WEIGHT_NATIVE_A9FU",
                "reference_date": requested,
                "weight": 700.0,
                "weight_base_year": 2026,
            }
        ],
    )

    assert main._collect(main._parse_args(["--no-watch"]), engine) == 0
    assert calls[:5] == [
        "init_db",
        "table38:2025-01-01",
        "mm23",
        "rates",
        "snapshots",
    ]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM collector_ons_ex_cpi.time_series")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM collector_ons_ex_cpi.metadata")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM collector_ons_ex_cpi.original_weights")).scalar() == 1
        assert conn.execute(
            text("SELECT reference_date, value FROM collector_ons_ex_cpi.time_series")
        ).one() == (requested, 102.5)
