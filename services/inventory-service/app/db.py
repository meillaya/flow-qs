"""SQLite inventory persistence with atomic reserve/release."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

DB_PATH = os.getenv("INVENTORY_DB_PATH", "inventory.db")
_CATALOG_SKUS = [f"SKU-{i:03d}" for i in range(1, 21)]
DEFAULT_STOCK = 100


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(
        DB_PATH,
        timeout=10.0,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    """Create tables and seed inventory for all 20 catalog products."""
    with _connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                sku TEXT PRIMARY KEY,
                available INTEGER NOT NULL,
                reserved INTEGER NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        row = conn.execute("SELECT 1 FROM inventory LIMIT 1").fetchone()
        if row is None:
            now = _now()
            conn.executemany(
                "INSERT INTO inventory (sku, available, reserved, updated_at) VALUES (?, ?, ?, ?)",
                [(sku, DEFAULT_STOCK, 0, now) for sku in _CATALOG_SKUS],
            )


def get_inventory(sku: str) -> dict[str, Any]:
    """Return stock row for a single SKU."""
    with _connection() as conn:
        row = conn.execute(
            "SELECT sku, available, reserved, updated_at FROM inventory WHERE sku = ?",
            (sku,),
        ).fetchone()
    if row is None:
        raise KeyError(sku)
    return dict(row)


def list_inventory() -> list[dict[str, Any]]:
    """Return stock rows for all SKUs."""
    with _connection() as conn:
        rows = conn.execute(
            "SELECT sku, available, reserved, updated_at FROM inventory ORDER BY sku"
        ).fetchall()
    return [dict(row) for row in rows]


def reserve_stock(sku: str, quantity: int) -> dict[str, Any]:
    """Atomically reserve stock if enough is available.

    Returns a dict describing the result. Raises ``KeyError`` if the SKU
    does not exist and ``ValueError`` if ``quantity`` is not positive.
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    now = _now()
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """
            UPDATE inventory
            SET available = available - ?, reserved = reserved + ?, updated_at = ?
            WHERE sku = ? AND available >= ?
            """,
            (quantity, quantity, now, sku, quantity),
        )
        if cur.rowcount == 0:
            row = conn.execute(
                "SELECT available, reserved FROM inventory WHERE sku = ?", (sku,)
            ).fetchone()
            conn.rollback()
            if row is None:
                raise KeyError(sku)
            return {
                "success": False,
                "available": row["available"],
                "reserved": row["reserved"],
                "reserved_quantity": 0,
                "message": "Insufficient stock",
            }

        row = conn.execute(
            "SELECT available, reserved FROM inventory WHERE sku = ?", (sku,)
        ).fetchone()
        conn.commit()

    return {
        "success": True,
        "available": row["available"],
        "reserved": row["reserved"],
        "reserved_quantity": quantity,
        "message": "Reserved",
    }


def release_stock(sku: str, quantity: int) -> dict[str, Any]:
    """Atomically release previously reserved stock.

    Returns a dict describing the result. Raises ``KeyError`` if the SKU
    does not exist and ``ValueError`` if ``quantity`` is not positive.
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    now = _now()
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """
            UPDATE inventory
            SET available = available + ?, reserved = reserved - ?, updated_at = ?
            WHERE sku = ? AND reserved >= ?
            """,
            (quantity, quantity, now, sku, quantity),
        )
        if cur.rowcount == 0:
            row = conn.execute(
                "SELECT available, reserved FROM inventory WHERE sku = ?", (sku,)
            ).fetchone()
            conn.rollback()
            if row is None:
                raise KeyError(sku)
            return {
                "success": False,
                "available": row["available"],
                "reserved": row["reserved"],
                "released_quantity": 0,
                "message": "Insufficient reserved stock",
            }

        row = conn.execute(
            "SELECT available, reserved FROM inventory WHERE sku = ?", (sku,)
        ).fetchone()
        conn.commit()

    return {
        "success": True,
        "available": row["available"],
        "reserved": row["reserved"],
        "released_quantity": quantity,
        "message": "Released",
    }
