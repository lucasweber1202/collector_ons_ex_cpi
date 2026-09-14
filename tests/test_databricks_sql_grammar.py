"""Opt-in: parse every emitted statement with Spark's own SQL parser.

Databricks SQL is Spark SQL, and ``pyspark`` is already a declared dependency
because ``scripts/databricks_engine.py`` resolves its token through
``pyspark.dbutils``. Running the statements through the same ANTLR grammar
Databricks uses replaces a reviewer's reading of the SQL with the engine's own
verdict on it.

What this proves: every statement is valid Spark SQL, including the identity
column, the informational primary keys, the MERGE shape and the named parameter
markers. What it does not prove: Unity Catalog semantics, Delta table
behaviour, permissions, or that a MERGE produces the intended rows on a real
warehouse. Those still require the Databricks gate recorded in COMPLIANCE.md.

Run: DATABRICKS_SQL_PARSE_TEST=1 python -m pytest tests/test_databricks_sql_grammar.py -q
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from tests.conftest import emitted_sql

pytestmark = pytest.mark.skipif(
    os.getenv("DATABRICKS_SQL_PARSE_TEST") != "1",
    reason="explicit opt-in: starting a local Spark session takes about half a minute",
)


@pytest.fixture(scope="module")
def spark_parser() -> Iterator[Any]:
    """Yield Spark's SQL parser from a throwaway local session."""
    pyspark = pytest.importorskip("pyspark")
    os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
    session = (
        pyspark.sql.SparkSession.builder.master("local[1]")
        .appName("collector_ons_ex_cpi-sql-grammar")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    try:
        yield session._jsparkSession.sessionState().sqlParser()
    finally:
        session.stop()


def test_every_emitted_statement_parses(spark_parser: Any) -> None:
    """A statement Spark's grammar rejects would fail on Databricks at runtime."""
    statements = emitted_sql()
    assert statements, "the statement inventory must not be empty"
    failures: dict[str, str] = {}
    for name, sql in sorted(statements.items()):
        try:
            spark_parser.parsePlan(sql)
        except Exception as exc:  # noqa: BLE001 -- the parser raises a Java exception
            failures[name] = str(exc).splitlines()[0]
    assert not failures, f"Spark SQL rejected {len(failures)} statements: {failures}"


def test_the_check_can_actually_fail(spark_parser: Any) -> None:
    """A gate that cannot fail proves nothing; PostgreSQL-only syntax must be rejected."""
    with pytest.raises(Exception, match=r"(?is)parse|syntax|unsupported"):
        spark_parser.parsePlan("INSERT INTO s.t (a) VALUES (1) ON CONFLICT (a) DO UPDATE SET a = 2")
