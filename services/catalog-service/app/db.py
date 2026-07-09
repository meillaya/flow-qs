"""SQLite database helpers using the stdlib sqlite3 module."""

import os
import sqlite3
from contextlib import contextmanager
from typing import Any

DB_PATH = os.environ.get("CATALOG_DB_PATH", "catalog.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    price_cents INTEGER NOT NULL,
    sku TEXT UNIQUE NOT NULL
);
"""


def ensure_db_dir() -> None:
    """Create the directory for the SQLite database if it does not exist."""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)


@contextmanager
def get_connection():
    """Yield a SQLite connection with row factory enabled."""
    ensure_db_dir()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Initialize the products table."""
    with get_connection() as conn:
        conn.executescript(SCHEMA)


def row_to_product(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite3 Row to a plain dict."""
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "price_cents": row["price_cents"],
        "sku": row["sku"],
    }


def list_products() -> list[dict[str, Any]]:
    """Return all products ordered by name."""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM products ORDER BY name").fetchall()
    return [row_to_product(row) for row in rows]


def get_product(product_id: str) -> dict[str, Any] | None:
    """Return a single product by id, or None if not found."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    return row_to_product(row) if row else None


def create_product(product: dict[str, Any]) -> dict[str, Any]:
    """Insert a new product and return it."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO products (id, name, description, price_cents, sku)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                product["id"],
                product["name"],
                product["description"],
                product["price_cents"],
                product["sku"],
            ),
        )
    return product


def search_products(query: str) -> list[dict[str, Any]]:
    """Search products by name (case-insensitive substring match)."""
    like = f"%{query}%"
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM products WHERE name LIKE ? ORDER BY name",
            (like,),
        ).fetchall()
    return [row_to_product(row) for row in rows]


def count_products() -> int:
    """Return the number of products in the table."""
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM products").fetchone()
    return row[0] if row else 0


def check_connection() -> bool:
    """Return True if SQLite responds to a simple query."""
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1")
        return True
    except sqlite3.Error:
        return False
