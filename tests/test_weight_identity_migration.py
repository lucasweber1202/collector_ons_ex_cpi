"""Legacy weight migration keeps values, vintages and timestamps intact."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from scripts.weight_identity import component_rows, migrate_legacy_weight_ids, upsert_crosswalk


def test_migration_preserves_every_measure_and_is_idempotent(engine: Engine) -> None:
    component = next(row for row in component_rows() if row["role"] == "exclusion")
    native, new = component["native_weight_cdid"], component["series_id"]
    assert native is not None
    collected = datetime(2026, 2, 12, 11, 30)  # noqa: DTZ001
    with engine.begin() as conn:
        for table, prefix, extra in (
            ("weights", "EXCPI_SHARE_NATIVE_", ""),
            ("original_weights", "EXCPI_WEIGHT_NATIVE_", ", weight_base_year"),
        ):
            for vintage, weight in ((date(2025, 3, 1), 0.7), (date(2026, 3, 1), 0.8)):
                conn.execute(
                    text(
                        f"INSERT INTO collector_ons_ex_cpi.{table} "
                        f"(series_id, reference_date, vintage_date, weight, collected_at{extra}) "
                        f"VALUES (:sid, :ref, :vintage, :weight, :collected{', :year' if extra else ''})"
                    ),
                    {
                        "sid": prefix + native,
                        "ref": date(2025, 1, 1),
                        "vintage": vintage,
                        "weight": weight,
                        "collected": collected,
                        "year": 2025,
                    },
                )
        migrate_legacy_weight_ids(conn)
        upsert_crosswalk(conn)
        migrate_legacy_weight_ids(conn)
        upsert_crosswalk(conn)
        for table in ("weights", "original_weights"):
            rows = conn.execute(
                text(
                    f"SELECT series_id, vintage_date, weight, collected_at "
                    f"FROM collector_ons_ex_cpi.{table} ORDER BY vintage_date"
                )
            ).all()
            assert rows == [
                (new, date(2025, 3, 1), 0.7, collected),
                (new, date(2026, 3, 1), 0.8, collected),
            ]
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM collector_ons_ex_cpi.weight_component_crosswalk")
            ).scalar_one()
            == 30
        )


def test_migration_fails_on_colliding_natural_key_without_changing_rows(engine: Engine) -> None:
    component = next(row for row in component_rows() if row["role"] == "exclusion")
    native, new = component["native_weight_cdid"], component["series_id"]
    assert native is not None
    with engine.begin() as conn:
        for sid in ("EXCPI_SHARE_NATIVE_" + native, new):
            conn.execute(
                text(
                    "INSERT INTO collector_ons_ex_cpi.weights "
                    "(series_id, reference_date, vintage_date, weight, collected_at) "
                    "VALUES (:sid, '2025-01-01', '2026-01-01', 0.5, '2026-01-02 00:00:00')"
                ),
                {"sid": sid},
            )
        with pytest.raises(ValueError, match="collisions"):
            migrate_legacy_weight_ids(conn)
        assert (
            conn.execute(
                text("SELECT COUNT(DISTINCT series_id) FROM collector_ons_ex_cpi.weights")
            ).scalar_one()
            == 2
        )
