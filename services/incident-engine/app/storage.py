"""SQLite-backed persistence layer for incidents and their postmortem files."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "/data/incidents.db"
DEFAULT_POSTMORTEM_DIR = "/postmortems"


def _utcnow_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(UTC).isoformat()


@dataclass
class IncidentRecord:
    """Metadata for a single incident stored in SQLite."""

    id: str
    alertname: str
    service: str
    severity: str
    slo: str
    status: str  # 'firing' | 'resolved' | 'unknown'
    starts_at: str
    ends_at: str | None
    fingerprint: str
    created_at: str
    updated_at: str
    summary: str
    file_path: str
    annotations: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    correlated_chaos: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict representation."""
        data = asdict(self)
        return data


class IncidentStore:
    """Thin wrapper around SQLite for incident metadata + postmortem files."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH, postmortem_dir: str = DEFAULT_POSTMORTEM_DIR) -> None:
        self.db_path = db_path
        self.postmortem_dir = Path(postmortem_dir)
        self.postmortem_dir.mkdir(parents=True, exist_ok=True)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        """Open a SQLite connection with sensible defaults."""
        conn = sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        """Yield a cursor guarded by the instance lock."""
        with self._lock:
            conn = self._connect()
            try:
                yield conn.cursor()
                conn.commit()
            finally:
                conn.close()

    def _init_schema(self) -> None:
        """Create the incidents table if it does not already exist."""
        with self._cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    alertname TEXT NOT NULL,
                    service TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    slo TEXT,
                    status TEXT NOT NULL,
                    starts_at TEXT NOT NULL,
                    ends_at TEXT,
                    fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    annotations_json TEXT NOT NULL DEFAULT '{}',
                    labels_json TEXT NOT NULL DEFAULT '{}',
                    timeline_json TEXT NOT NULL DEFAULT '[]',
                    correlated_chaos_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_incidents_fingerprint ON incidents(fingerprint)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_incidents_service ON incidents(service)")

    def upsert(self, record: IncidentRecord) -> IncidentRecord:
        """Insert or update an incident keyed by id."""
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO incidents (
                    id, alertname, service, severity, slo, status,
                    starts_at, ends_at, fingerprint, created_at, updated_at,
                    summary, file_path, annotations_json, labels_json,
                    timeline_json, correlated_chaos_json
                ) VALUES (
                    :id, :alertname, :service, :severity, :slo, :status,
                    :starts_at, :ends_at, :fingerprint, :created_at, :updated_at,
                    :summary, :file_path, :annotations_json, :labels_json,
                    :timeline_json, :correlated_chaos_json
                )
                ON CONFLICT(id) DO UPDATE SET
                    alertname=excluded.alertname,
                    service=excluded.service,
                    severity=excluded.severity,
                    slo=excluded.slo,
                    status=excluded.status,
                    starts_at=excluded.starts_at,
                    ends_at=excluded.ends_at,
                    fingerprint=excluded.fingerprint,
                    updated_at=excluded.updated_at,
                    summary=excluded.summary,
                    file_path=excluded.file_path,
                    annotations_json=excluded.annotations_json,
                    labels_json=excluded.labels_json,
                    timeline_json=excluded.timeline_json,
                    correlated_chaos_json=excluded.correlated_chaos_json
                """,
                {
                    "id": record.id,
                    "alertname": record.alertname,
                    "service": record.service,
                    "severity": record.severity,
                    "slo": record.slo,
                    "status": record.status,
                    "starts_at": record.starts_at,
                    "ends_at": record.ends_at,
                    "fingerprint": record.fingerprint,
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                    "summary": record.summary,
                    "file_path": record.file_path,
                    "annotations_json": json.dumps(record.annotations),
                    "labels_json": json.dumps(record.labels),
                    "timeline_json": json.dumps(record.timeline),
                    "correlated_chaos_json": json.dumps(record.correlated_chaos),
                },
            )
        return record

    def list_all(self) -> list[IncidentRecord]:
        """Return all incidents ordered by creation time, newest first."""
        with self._cursor() as cur:
            rows = cur.execute(
                """
                SELECT * FROM incidents
                ORDER BY datetime(created_at) DESC
                """
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get(self, incident_id: str) -> IncidentRecord | None:
        """Return a single incident by id or None."""
        with self._cursor() as cur:
            row = cur.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def find_open_for_fingerprint(self, fingerprint: str) -> IncidentRecord | None:
        """Return the most recent open incident for a fingerprint, if any."""
        with self._cursor() as cur:
            row = cur.execute(
                """
                SELECT * FROM incidents
                WHERE fingerprint = ? AND status IN ('firing', 'unknown')
                ORDER BY datetime(created_at) DESC
                LIMIT 1
                """,
                (fingerprint,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def write_postmortem(self, record: IncidentRecord, content: str) -> Path:
        """Persist a postmortem.md file for the given incident."""
        path = Path(record.file_path)
        if not path.is_absolute():
            path = self.postmortem_dir / path.name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        record.file_path = str(path)
        return path

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> IncidentRecord:
        """Materialise an IncidentRecord from a sqlite3 Row."""
        return IncidentRecord(
            id=row["id"],
            alertname=row["alertname"],
            service=row["service"],
            severity=row["severity"],
            slo=row["slo"] or "",
            status=row["status"],
            starts_at=row["starts_at"],
            ends_at=row["ends_at"],
            fingerprint=row["fingerprint"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            summary=row["summary"],
            file_path=row["file_path"],
            annotations=json.loads(row["annotations_json"] or "{}"),
            labels=json.loads(row["labels_json"] or "{}"),
            timeline=json.loads(row["timeline_json"] or "[]"),
            correlated_chaos=json.loads(row["correlated_chaos_json"] or "[]"),
        )


def build_postmortem_path(alertname: str, starts_at: str) -> str:
    """Build a deterministic postmortem filename from alertname + timestamp."""
    safe_alert = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in alertname.lower()).strip("-") or "incident"
    # Compact the timestamp to a filesystem-friendly form
    compact_ts = starts_at.replace(":", "").replace("-", "").replace("+", "_Z").replace(".", "_")[:17]
    return f"postmortem-{compact_ts}-{safe_alert}.md"


def default_db_path() -> str:
    """Return the configured SQLite path or the default."""
    return os.getenv("INCIDENT_DB_PATH", DEFAULT_DB_PATH)


def default_postmortem_dir() -> str:
    """Return the configured postmortem directory or the default."""
    return os.getenv("POSTMORTEM_DIR", DEFAULT_POSTMORTEM_DIR)
