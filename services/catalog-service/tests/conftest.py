"""Pytest fixtures and configuration."""

import os
import tempfile
from contextlib import suppress

os.environ["OTEL_SERVICE_NAME"] = "catalog-service-test"
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
os.environ["OTEL_TRACES_EXPORTER"] = "none"

import pytest
from fastapi.testclient import TestClient

from app import db, seed


@pytest.fixture(autouse=True)
def fresh_database(monkeypatch):
    """Provide an isolated on-disk SQLite database for each test."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        path = tmp.name
    monkeypatch.setenv("CATALOG_DB_PATH", path)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    seed.seed_if_empty()
    yield
    with suppress(FileNotFoundError):
        os.unlink(path)


@pytest.fixture
def client():
    """Return a FastAPI TestClient."""
    from app.main import app

    return TestClient(app)
