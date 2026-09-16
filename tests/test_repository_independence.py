"""Regression gates for standalone repository portability."""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_SUFFIXES = {".py", ".toml", ".txt", ".yml", ".yaml", ".ps1", ".sh"}
PATH_PATTERNS = (
    re.compile(r"\.\.[/\\]collector_[a-z0-9_]+", re.IGNORECASE),
    re.compile(r"(?:^|\s)-e\s+\.\.[/\\]", re.IGNORECASE),
    re.compile(r"C:\\Users\\|/Users/|/home/[A-Za-z0-9_.-]+/", re.IGNORECASE),
)


def repository_files() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and ".venv" not in path.parts
        and path.name != Path(__file__).name
        and path.suffix.lower() in SCAN_SUFFIXES
    ]


def test_no_cross_repository_python_imports() -> None:
    findings: list[str] = []
    for path in repository_files():
        if path.suffix != ".py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            )
            findings.extend(
                f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', 0)}:{name}"
                for name in names
                if name.startswith("collector_")
            )
    assert not findings, "Cross-repository imports found: " + "; ".join(findings)


def test_no_sibling_paths_editable_installs_or_personal_absolute_paths() -> None:
    findings = [
        f"{path.relative_to(ROOT)} matches {pattern.pattern}"
        for path in repository_files()
        for pattern in PATH_PATTERNS
        if pattern.search(path.read_text(encoding="utf-8", errors="replace"))
    ]
    assert not findings, "Non-portable references found: " + "; ".join(findings)


def test_repository_contains_no_symlinks() -> None:
    links = [
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if path.is_symlink() and ".venv" not in path.parts
    ]
    assert not links, f"Repository symlinks are forbidden: {links}"


def test_public_imports_are_side_effect_free_without_runtime_configuration() -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("DATABRICKS_")}
    for key in (
        "PROD",
        "COLLECTOR_DB_URL",
        "DBX_SERVER_HOSTNAME",
        "DBX_HTTP_PATH",
        "AKV_VAULT_URL",
    ):
        env.pop(key, None)
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT)})
    with tempfile.TemporaryDirectory() as directory:
        completed = subprocess.run(
            [sys.executable, "-c", "import scripts.config, scripts.db, main"],
            cwd=directory,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    assert completed.returncode == 0, completed.stderr
