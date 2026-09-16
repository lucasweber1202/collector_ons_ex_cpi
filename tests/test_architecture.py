from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_cross_repository_python_dependencies() -> None:
    forbidden_fragments = ("../collector_", "..\\\\collector_", "-e ../", "PYTHONPATH")
    for path in ROOT.rglob("*.py"):
        if path == Path(__file__).resolve():
            continue
        if any(part in {".git", ".venv", "venv"} for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        assert not any(fragment in source for fragment in forbidden_fragments), path
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith("collector_"), f"cross-repo import {name} in {path}"
