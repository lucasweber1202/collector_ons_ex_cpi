"""The declared dependency surface must stay mirrored and free of banned packages."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from scripts.config import ROOT_DIR

BANNED = ("requests", "python-dotenv", "sqlalchemy-utils", "alembic", "pydantic", "attrs")


def _requirements() -> list[str]:
    text = (Path(ROOT_DIR) / "requirements.txt").read_text(encoding="utf-8")
    return sorted(line.strip() for line in text.splitlines() if line.strip())


def _pyproject() -> list[str]:
    data = tomllib.loads((Path(ROOT_DIR) / "pyproject.toml").read_text(encoding="utf-8"))
    return sorted(data["project"]["dependencies"])


def test_requirements_and_pyproject_are_mirrored() -> None:
    assert _requirements() == _pyproject()


@pytest.mark.parametrize("package", BANNED)
def test_forbidden_packages_are_absent(package: str) -> None:
    assert not any(entry.startswith(package) for entry in _requirements())


def test_databricks_token_resolution_surface_is_declared() -> None:
    """databricks_engine.py reads pyspark.dbutils in a notebook/job context."""
    assert "pyspark==4.1.1" in _requirements()
    source = (Path(ROOT_DIR) / "scripts" / "databricks_engine.py").read_text(encoding="utf-8")
    assert "from pyspark.dbutils import DBUtils" in source
