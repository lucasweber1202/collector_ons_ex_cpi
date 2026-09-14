"""Every statement the collector sends must stay in the PostgreSQL/Databricks subset.

PostgreSQL and Databricks SQL share no spelling for a 64-bit float, and the one
spelling both accept, FLOAT, means 8 bytes on PostgreSQL and 4 on Databricks.
These tests pin the mapping and the portable-subset rules so a future edit
cannot silently halve precision or introduce a dialect-only construct.
"""

from __future__ import annotations

import re

import pytest

from scripts.init_db import CREATE_TIME_SERIES_TABLE, CREATE_WEIGHTS_TABLE, double_type
from tests.conftest import emitted_sql

STATEMENTS = emitted_sql()

# Constructs that exist in PostgreSQL but not in Databricks SQL, or that mean
# different things in the two engines.
POSTGRES_ONLY = (
    r"\bSERIAL\b",
    r"\bJSONB\b",
    r"\bON CONFLICT\b",
    r"\bRETURNING\b",
    r"\bILIKE\b",
    r"\bDISTINCT ON\b",
    r"\bFILTER\s*\(",
    r"::",
    r"\bDOUBLE PRECISION\b",
    r"\bTEXT\b(?!\s*[,)])",
    r"\bTIMESTAMPTZ\b",
    r"\bNOW\(\)",
    r"\bCURRENT_TIMESTAMP\b",
)
# Every literal the collector is allowed to inline. Anything else means a value
# stopped travelling as a bound parameter.
REVIEWED_LITERALS = frozenset({"CPI%", "_", ""})


def test_postgresql_uses_double_precision() -> None:
    """PostgreSQL has no DOUBLE type; the ANSI spelling is required."""
    assert double_type("postgresql") == "DOUBLE PRECISION"


def test_databricks_uses_double() -> None:
    """Spark lists DOUBLE as the only alias for DoubleType."""
    assert double_type("databricks") == "DOUBLE"


def test_unknown_dialect_falls_back_to_double() -> None:
    assert double_type("sqlite") == "DOUBLE"


@pytest.mark.parametrize("template", [CREATE_TIME_SERIES_TABLE, CREATE_WEIGHTS_TABLE])
def test_float_is_never_emitted(template: str) -> None:
    """FLOAT would be accepted by both engines at different precisions."""
    for dialect in ("postgresql", "databricks", "sqlite"):
        rendered = template.format(double=double_type(dialect))
        assert " FLOAT" not in rendered.upper()
        assert " REAL" not in rendered.upper()


def test_the_inventory_covers_every_table_and_operation() -> None:
    """A statement added to the collector must appear here, or it is unreviewed."""
    assert len(STATEMENTS) >= 31
    for table in ("time_series", "weights", "original_weights", "metadata"):
        for operation in ("insert", "merge", "update"):
            assert f"{table}.{operation}" in STATEMENTS
    assert {name for name in STATEMENTS if name.startswith("ddl.")} == {
        "ddl.schema",
        "ddl.metadata",
        "ddl.time_series",
        "ddl.weights",
        "ddl.original_weights",
        "ddl.logs",
    }


@pytest.mark.parametrize("name", sorted(STATEMENTS))
def test_emitted_sql_stays_in_the_portable_subset(name: str) -> None:
    statement = STATEMENTS[name]
    for pattern in POSTGRES_ONLY:
        assert not re.search(pattern, statement, re.IGNORECASE), (
            f"{name} uses a non-portable construct matching {pattern}: {statement}"
        )


@pytest.mark.parametrize("name", sorted(STATEMENTS))
def test_emitted_sql_inlines_only_reviewed_literals(name: str) -> None:
    """Every value travels as a named parameter; only constants are inlined."""
    literals = set(re.findall(r"'([^']*)'", STATEMENTS[name]))
    assert literals <= REVIEWED_LITERALS, f"{name} inlines {sorted(literals - REVIEWED_LITERALS)}"


@pytest.mark.parametrize("name", sorted(STATEMENTS))
def test_emitted_sql_addresses_only_the_collector_schema(name: str) -> None:
    from scripts.config import SCHEMA_NAME

    assert SCHEMA_NAME in STATEMENTS[name]
    others = set(
        re.findall(
            r"\b(\w+)\.(?:metadata|time_series|weights|original_weights|logs)\b", STATEMENTS[name]
        )
    )
    assert others <= {SCHEMA_NAME}, f"{name} addresses {sorted(others - {SCHEMA_NAME})}"


def test_merge_matches_on_the_full_natural_key() -> None:
    """Databricks treats primary keys as informational, so the ON clause is the key."""
    from scripts import original_weights, time_series, weights

    for module in (time_series, weights, original_weights):
        condition = str(module._merge_statement(1)).split(" ON ", 1)[1]
        for column in module._KEY_COLUMNS:
            assert f"target.{column} = source.{column}" in condition
