"""Legacy weight migration keeps values, vintages and timestamps intact."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from scripts.weight_identity import (
    component_rows,
    earliest_legacy_weight_month,
    migrate_legacy_weight_ids,
    upsert_crosswalk,
)


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


def test_incremental_migration_discovers_earliest_legacy_regime(engine: Engine) -> None:
    component = next(row for row in component_rows() if row["role"] == "exclusion")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO collector_ons_ex_cpi.original_weights "
                "(series_id, reference_date, vintage_date, weight, weight_base_year, collected_at) "
                "VALUES (:sid, '1996-01-01', '2025-01-01', 800, 1996, '2025-01-01 00:00:00')"
            ),
            {"sid": "EXCPI_WEIGHT_NATIVE_" + str(component["native_weight_cdid"])},
        )
    assert earliest_legacy_weight_month(engine) == date(1996, 1, 1)
    with engine.begin() as conn:
        migrate_legacy_weight_ids(conn)
    assert earliest_legacy_weight_month(engine) is None


def test_reconstructs_three_aggregates_and_regimes_from_persisted_rows(engine: Engine) -> None:
    """The reconstruction reads only stored SQL rows, including December bases."""
    pairs = [row for row in component_rows() if row["role"] != "headline"]
    aggregates = list(dict.fromkeys(str(row["aggregate_series_id"]) for row in pairs))[:3]
    collected = datetime(2026, 9, 1, 12, 0)  # noqa: DTZ001
    with engine.begin() as conn:
        upsert_crosswalk(conn)
        for aggregate in aggregates:
            members = [row for row in pairs if row["aggregate_series_id"] == aggregate]
            for member in members:
                sid = member["series_id"]
                assert sid is not None
                conn.execute(
                    text(
                        "INSERT OR IGNORE INTO collector_ons_ex_cpi.metadata "
                        "(series_id, name, country, observation_count, source_url, collected_at) "
                        "VALUES (:sid, :sid, 'GBP', 1, 'https://www.ons.gov.uk/', :collected)"
                    ),
                    {"sid": sid, "collected": collected},
                )
            for year in (2016, 2018, 2026):
                december = date(year - 1, 12, 1)
                for month in (1, 2):
                    period = date(year, month, 1)
                    for member in members:
                        sid = member["series_id"]
                        assert sid is not None
                        is_exclusion = member["role"] == "exclusion"
                        share = 0.8 if is_exclusion else 0.2
                        value = (110 if is_exclusion else 105) + month - 1
                        for ref, level in ((december, 100.0), (period, float(value))):
                            conn.execute(
                                text(
                                    "INSERT OR IGNORE INTO collector_ons_ex_cpi.time_series "
                                    "(series_id, reference_date, vintage_date, value, collected_at) "
                                    "VALUES (:sid, :ref, :vintage, :value, :collected)"
                                ),
                                {
                                    "sid": sid,
                                    "ref": ref,
                                    "vintage": collected.date(),
                                    "value": level,
                                    "collected": collected,
                                },
                            )
                        for table, column, weight in (
                            ("weights", "", share),
                            ("original_weights", ", weight_base_year", share * 1000),
                        ):
                            conn.execute(
                                text(
                                    f"INSERT INTO collector_ons_ex_cpi.{table} "
                                    f"(series_id, reference_date, vintage_date, weight, collected_at{column}) "
                                    f"VALUES (:sid, :ref, :vintage, :weight, :collected{', :year' if column else ''})"
                                ),
                                {
                                    "sid": sid,
                                    "ref": period,
                                    "vintage": collected.date(),
                                    "weight": weight,
                                    "collected": collected,
                                    "year": year,
                                },
                            )
                    headline = "EXCPI_INDEX_NATIVE_D7BT"
                    conn.execute(
                        text(
                            "INSERT OR IGNORE INTO collector_ons_ex_cpi.metadata "
                            "(series_id, name, country, observation_count, source_url, collected_at) "
                            "VALUES (:sid, :sid, 'GBP', 1, 'https://www.ons.gov.uk/', :collected)"
                        ),
                        {"sid": headline, "collected": collected},
                    )
                    for ref, level in ((december, 100.0), (period, 109.0 + month - 1)):
                        conn.execute(
                            text(
                                "INSERT OR IGNORE INTO collector_ons_ex_cpi.time_series "
                                "(series_id, reference_date, vintage_date, value, collected_at) "
                                "VALUES (:sid, :ref, :vintage, :value, :collected)"
                            ),
                            {
                                "sid": headline,
                                "ref": ref,
                                "vintage": collected.date(),
                                "value": level,
                                "collected": collected,
                            },
                        )

        # No Python parser objects or downloads enter this query: SQL joins
        # recover both component levels, December bases and operational shares.
        rows = conn.execute(
            text(
                "SELECT c.aggregate_series_id, w.reference_date, "
                "SUM(w.weight * cur.value / base.value) * hb.value AS reconstructed, "
                "hn.value AS published "
                "FROM collector_ons_ex_cpi.weight_component_crosswalk c "
                "JOIN collector_ons_ex_cpi.weights w ON w.series_id=c.series_id "
                "JOIN collector_ons_ex_cpi.time_series cur "
                "ON cur.series_id=c.series_id AND cur.reference_date=w.reference_date "
                "JOIN collector_ons_ex_cpi.time_series base "
                "ON base.series_id=c.series_id AND base.reference_date="
                "date(w.reference_date, 'start of year', '-1 month') "
                "JOIN collector_ons_ex_cpi.time_series hn "
                "ON hn.series_id='EXCPI_INDEX_NATIVE_D7BT' AND hn.reference_date=w.reference_date "
                "JOIN collector_ons_ex_cpi.time_series hb "
                "ON hb.series_id='EXCPI_INDEX_NATIVE_D7BT' AND hb.reference_date=base.reference_date "
                "WHERE c.role IN ('exclusion', 'complement') "
                "GROUP BY c.aggregate_series_id, w.reference_date, hb.value, hn.value"
            )
        ).all()
        assert len(rows) == 3 * 3 * 2
        assert all(abs(row.reconstructed - row.published) < 1e-9 for row in rows)
        for table in ("weights", "original_weights"):
            assert (
                conn.execute(
                    text(
                        f"SELECT COUNT(*) FROM collector_ons_ex_cpi.{table} w "
                        "LEFT JOIN collector_ons_ex_cpi.metadata m ON m.series_id=w.series_id "
                        "WHERE m.series_id IS NULL"
                    )
                ).scalar_one()
                == 0
            )
