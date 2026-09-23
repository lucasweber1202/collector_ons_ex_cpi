"""The operational share layer, and the claim that it rebuilds the index.

`original_weights` and `weights` are not two copies of one thing. The first is
the published MM23 basket in parts per thousand; the second is the normalised
system that actually reconstructs an aggregate. These tests pin the difference,
because collapsing them would silently reintroduce the reconstruction error the
share layer exists to remove.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from scripts.reconciliation import (
    RECONSTRUCTION_TOLERANCE,
    build_operational_shares,
    operational_share_id,
    parse_operational_share_id,
    reconstruction_checks,
)
from scripts.special_aggregates import (
    EX_CPI_SPECIAL_AGGREGATES,
    HEADLINE_INDEX_CDID,
    MM23SpecialPanel,
)
from scripts.weights import upsert_weights

JANUARY = date(2026, 1, 1)
# Naive, matching tests/test_original_persistence.py: the SQLite stand-in
# uses sqlite3's built-in TIMESTAMP converter, which cannot read a tz-aware
# isoformat. PostgreSQL stores the aware instants the pipeline really writes.
FIRST = datetime(2026, 2, 1)  # noqa: DTZ001
LATER = datetime(2026, 3, 1)  # noqa: DTZ001


def _regime(exclusion: float = 800.0, complement: float = 200.0) -> dict[str, float]:
    values: dict[str, float] = {}
    for aggregate in EX_CPI_SPECIAL_AGGREGATES:
        values[aggregate["weight_cdid"]] = exclusion
        values[aggregate["complement_weight_cdid"]] = complement
    return values


# -- identity -------------------------------------------------------------


@pytest.mark.parametrize(
    "cdid",
    sorted(
        {row["weight_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES}
        | {row["complement_weight_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES}
    ),
)
def test_share_id_round_trips_for_every_weight_the_repo_can_emit(cdid: str) -> None:
    series_id = operational_share_id(cdid)
    assert series_id == f"EXCPI_SHARE_NATIVE_{cdid}"
    assert parse_operational_share_id(series_id) == ("EXCPI", "SHARE", "NATIVE", cdid)


@pytest.mark.parametrize(
    "bad", ["", "EXCPI", "EXCPI_SHARE_NATIVE", "EXCPI_WEIGHT_NATIVE_A9FU", "EXCPI_SHARE_NATIVE_"]
)
def test_malformed_share_ids_raise_value_error(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_operational_share_id(bad)


def test_share_identity_never_collides_with_the_official_weight_identity() -> None:
    """Both layers key on the same CDID, so the prefixes must stay distinct."""
    from scripts.special_aggregate_vintages import mm23_original_weight_id

    for aggregate in EX_CPI_SPECIAL_AGGREGATES:
        cdid = aggregate["weight_cdid"]
        assert operational_share_id(cdid) != mm23_original_weight_id(cdid)


# -- construction ---------------------------------------------------------


def test_shares_are_normalised_within_each_pair() -> None:
    rows = build_operational_shares({JANUARY: _regime()})
    assert len(rows) == 2 * len(EX_CPI_SPECIAL_AGGREGATES)
    by_id = {row["series_id"]: row["weight"] for row in rows}
    for aggregate in EX_CPI_SPECIAL_AGGREGATES:
        exclusion = by_id[operational_share_id(aggregate["weight_cdid"])]
        complement = by_id[operational_share_id(aggregate["complement_weight_cdid"])]
        assert exclusion == pytest.approx(0.8)
        assert complement == pytest.approx(0.2)
        assert exclusion + complement == pytest.approx(1.0)


def test_shares_normalise_against_their_own_pair_not_a_hardcoded_thousand() -> None:
    """A pair that does not total 1000 still yields shares summing to one.

    The discrepancy stays visible in original_weights, where the published
    values are stored unchanged; it is not silently spread into the shares.
    """
    rows = build_operational_shares({JANUARY: _regime(exclusion=700.0, complement=200.0)})
    by_id = {row["series_id"]: row["weight"] for row in rows}
    aggregate = EX_CPI_SPECIAL_AGGREGATES[0]
    exclusion = by_id[operational_share_id(aggregate["weight_cdid"])]
    complement = by_id[operational_share_id(aggregate["complement_weight_cdid"])]
    assert exclusion == pytest.approx(700 / 900)
    assert exclusion + complement == pytest.approx(1.0)


def test_every_share_lands_inside_the_unit_interval() -> None:
    rows = build_operational_shares({JANUARY: _regime()})
    assert all(0.0 <= row["weight"] <= 1.0 for row in rows)


def test_a_missing_half_of_a_pair_is_refused() -> None:
    partial = _regime()
    del partial[EX_CPI_SPECIAL_AGGREGATES[0]["complement_weight_cdid"]]
    with pytest.raises(ValueError, match="Missing MM23 weight pair"):
        build_operational_shares({JANUARY: partial})


def test_a_non_positive_pair_is_refused() -> None:
    with pytest.raises(ValueError, match="Non-positive MM23 weight pair"):
        build_operational_shares({JANUARY: _regime(exclusion=0.0, complement=0.0)})


# -- persistence keeps the two layers apart -------------------------------


def test_published_basket_points_cannot_be_stored_as_shares(engine: Engine) -> None:
    """The guard that stops original_weights leaking into weights."""
    with engine.begin() as conn, pytest.raises(ValueError, match=r"outside \[0, 1\]"):
        upsert_weights(
            conn,
            [{"series_id": "EXCPI_SHARE_NATIVE_A9FU", "reference_date": JANUARY, "weight": 700.0}],
            FIRST,
        )


def test_shares_persist_and_a_rerun_is_a_no_op(engine: Engine) -> None:
    rows = [{"series_id": "EXCPI_SHARE_NATIVE_A9FU", "reference_date": JANUARY, "weight": 0.78}]
    collected = FIRST
    with engine.begin() as conn:
        assert upsert_weights(conn, rows, collected) == (1, 0)
    with engine.begin() as conn:
        assert upsert_weights(conn, rows, collected) == (0, 0)
        stored = (
            conn.execute(text("SELECT weight FROM collector_ons_ex_cpi.weights")).scalars().all()
        )
    assert stored == [0.78]


def test_a_revised_share_on_a_later_day_opens_a_new_vintage(engine: Engine) -> None:
    key = {"series_id": "EXCPI_SHARE_NATIVE_A9FU", "reference_date": JANUARY}
    with engine.begin() as conn:
        upsert_weights(conn, [{**key, "weight": 0.78}], FIRST)
    with engine.begin() as conn:
        assert upsert_weights(conn, [{**key, "weight": 0.79}], LATER) == (0, 1)
    with engine.connect() as conn:
        stored = (
            conn.execute(
                text("SELECT weight FROM collector_ons_ex_cpi.weights ORDER BY vintage_date")
            )
            .scalars()
            .all()
        )
    assert stored == [0.78, 0.79]


# -- the reconstruction itself --------------------------------------------


def _panel(index: float, complement: float, headline: float) -> MM23SpecialPanel:
    """A two-month panel where December is the link base."""
    december, month = date(2025, 12, 1), JANUARY
    base = {}
    now = {}
    for aggregate in EX_CPI_SPECIAL_AGGREGATES:
        base[aggregate["index_cdid"]] = 100.0
        base[aggregate["complement_index_cdid"]] = 100.0
        now[aggregate["index_cdid"]] = index
        now[aggregate["complement_index_cdid"]] = complement
    base[HEADLINE_INDEX_CDID] = 100.0
    now[HEADLINE_INDEX_CDID] = headline
    return MM23SpecialPanel({}, {december: base, month: now}, {})


def test_an_exact_reconstruction_passes() -> None:
    """0.8*110 + 0.2*120 = 112, all rebased on a December of 100."""
    checks = reconstruction_checks(_panel(110.0, 120.0, 112.0), {JANUARY: _regime()})
    assert len(checks) == len(EX_CPI_SPECIAL_AGGREGATES)
    assert all(check["passed"] for check in checks)
    for check in checks:
        residual = check["residual"]
        assert isinstance(residual, float)
        assert abs(residual) < 1e-9


def test_a_reconstruction_outside_tolerance_fails_rather_than_rounding_away() -> None:
    drifted = 112.0 + RECONSTRUCTION_TOLERANCE * 2
    checks = reconstruction_checks(_panel(110.0, 120.0, drifted), {JANUARY: _regime()})
    assert checks and not any(check["passed"] for check in checks)


def test_level_aggregation_is_not_what_is_being_checked() -> None:
    """Guards the distinction the share layer exists for.

    With a December base of 100 the two forms agree, so the panel here moves
    the base away from 100: a level-weighted average of the current indices
    would give 112, while the December-linked form gives something else, and
    the check must be scoring the latter.
    """
    december, month = date(2025, 12, 1), JANUARY
    aggregate = EX_CPI_SPECIAL_AGGREGATES[0]
    base = {
        aggregate["index_cdid"]: 90.0,
        aggregate["complement_index_cdid"]: 120.0,
        HEADLINE_INDEX_CDID: 96.0,
    }
    now = {
        aggregate["index_cdid"]: 110.0,
        aggregate["complement_index_cdid"]: 120.0,
        HEADLINE_INDEX_CDID: 112.0,
    }
    panel = MM23SpecialPanel({}, {december: base, month: now}, {})
    checks = reconstruction_checks(panel, {JANUARY: _regime()})
    assert len(checks) == 1
    level_form = 0.8 * 110.0 + 0.2 * 120.0
    linked_form = 96.0 * (0.8 * 110.0 / 90.0 + 0.2 * 120.0 / 120.0)
    reconstructed = checks[0]["reconstructed"]
    assert isinstance(reconstructed, float)
    assert reconstructed == pytest.approx(linked_form)
    assert reconstructed != pytest.approx(level_form)
