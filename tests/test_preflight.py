"""Regression tests for the environment preflight in both deployment modes."""

from __future__ import annotations

import pytest

from scripts import config


def test_local_mode_requires_the_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", "")

    assert config.missing_environment(prod=False) == ["COLLECTOR_DB_URL"]


def test_local_mode_passes_with_a_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql+psycopg2://u:p@localhost/db")

    assert config.missing_environment(prod=False) == []
    assert config.unresolved_credentials(prod=False) == []


def test_prod_mode_reports_every_missing_variable_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One run must surface the complete list, not fail on the first gap."""
    monkeypatch.setattr(config, "DBX_SERVER_HOSTNAME", "")
    monkeypatch.setattr(config, "DBX_HTTP_PATH", "")

    assert config.missing_environment(prod=True) == ["DBX_HTTP_PATH", "DBX_SERVER_HOSTNAME"]


def test_prod_mode_passes_with_both_databricks_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "DBX_SERVER_HOSTNAME", "adb-1.azuredatabricks.net")
    monkeypatch.setattr(config, "DBX_HTTP_PATH", "/sql/1.0/warehouses/abc")

    assert config.missing_environment(prod=True) == []


def test_prod_token_absence_is_deferred_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token may still come from the Databricks notebook/job context."""
    monkeypatch.setattr(config, "DBX_SERVER_HOSTNAME", "adb-1.azuredatabricks.net")
    monkeypatch.setattr(config, "DBX_HTTP_PATH", "/sql/1.0/warehouses/abc")
    monkeypatch.setattr(config, "AKV_VAULT_URL", "")
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

    assert config.missing_environment(prod=True) == []
    assert config.unresolved_credentials(prod=True) == ["DATABRICKS_TOKEN", "AKV_VAULT_URL"]


def test_prod_token_from_key_vault_is_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "AKV_VAULT_URL", "https://vault.vault.azure.net/")
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

    assert config.unresolved_credentials(prod=True) == []


def test_preflight_raises_listing_all_missing_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main._preflight must run before any engine or HTTP work."""
    import main

    monkeypatch.setattr(
        main, "missing_environment", lambda: ["DBX_HTTP_PATH", "DBX_SERVER_HOSTNAME"]
    )

    with pytest.raises(RuntimeError) as excinfo:
        main._preflight()

    assert "DBX_HTTP_PATH" in str(excinfo.value)
    assert "DBX_SERVER_HOSTNAME" in str(excinfo.value)
