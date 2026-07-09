"""Shared pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from app import db


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Use an isolated SQLite database for every test."""
    db_path = tmp_path / "inventory.db"
    monkeypatch.setattr("app.db.DB_PATH", str(db_path))
    db.init_db()
