"""Shared isolated SQLite fixture for persistence tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from scripts import init_db

SCHEMA = "collector_ons_ex_cpi"


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    created = create_engine(
        f"sqlite:///{tmp_path / 'main.db'}",
        connect_args={"detect_types": sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES},
    )
    schema_path = tmp_path / "collector.db"

    @event.listens_for(created, "connect")
    def _attach(dbapi_connection: sqlite3.Connection, _record: object) -> None:
        dbapi_connection.execute(f"ATTACH DATABASE '{schema_path}' AS {SCHEMA}")

    logs = init_db.CREATE_LOGS_TABLE.replace(
        "id BIGINT GENERATED ALWAYS AS IDENTITY", "id INTEGER PRIMARY KEY AUTOINCREMENT"
    ).replace(",\n    CONSTRAINT pk_logs PRIMARY KEY (id)", "")
    with created.begin() as conn:
        for statement in (
            init_db.CREATE_METADATA_TABLE,
            init_db.CREATE_TIME_SERIES_TABLE.format(double="DOUBLE"),
            init_db.CREATE_ORIGINAL_WEIGHTS_TABLE.format(double="DOUBLE"),
            init_db.CREATE_WEIGHTS_TABLE.format(double="DOUBLE"),
            logs,
        ):
            conn.execute(text(statement))
    try:
        yield created
    finally:
        created.dispose()
