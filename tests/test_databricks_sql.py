"""Minimal Spark SQL parser gate for every EX-CPI persistence surface."""

from __future__ import annotations

import pytest

from scripts import init_db, metadata, original_weights, run_logs, time_series


def _databricks_statements() -> list[str]:
    return [
        init_db.CREATE_SCHEMA,
        init_db.CREATE_METADATA_TABLE,
        init_db.CREATE_TIME_SERIES_TABLE.format(double=init_db.DEFAULT_DOUBLE_TYPE),
        init_db.CREATE_ORIGINAL_WEIGHTS_TABLE.format(double=init_db.DEFAULT_DOUBLE_TYPE),
        init_db.CREATE_LOGS_TABLE,
        str(time_series._insert_statement(1)),
        str(time_series._merge_statement(1)),
        str(time_series._LATEST_SQL),
        str(time_series._AGGREGATES_SQL),
        str(original_weights._insert_statement(1)),
        str(original_weights._merge_statement(1)),
        str(original_weights._LATEST_SQL),
        str(metadata._insert_statement(1)),
        str(metadata._merge_statement(1)),
        str(metadata._SELECT_SQL),
        str(run_logs._INSERT_SQL),
    ]


def test_all_emitted_sql_parses_as_spark_sql() -> None:
    try:
        from pyspark.sql import SparkSession

        spark = (
            SparkSession.builder.master("local[1]")
            .appName("ex-cpi-sql-parser")
            .config("spark.ui.enabled", "false")
            .getOrCreate()
        )
    except Exception as exc:  # noqa: BLE001 - environment capability probe
        pytest.skip(f"Spark SQL parser unavailable: {exc}")

    parser = spark._jsparkSession.sessionState().sqlParser()
    try:
        for statement in _databricks_statements():
            parser.parsePlan(statement)
    finally:
        spark.stop()
