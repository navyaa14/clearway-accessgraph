"""SQLite persistence: households, calibration, observations, incidents, actions,
inbox, idempotent event log, scene-graph edges and an append-only, hash-chained
evidence ledger (UPDATE is blocked by trigger; DELETE only via explicit purge)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS households(id TEXT PRIMARY KEY, name TEXT, need TEXT, token_hash TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS members(id TEXT PRIMARY KEY, household_id TEXT, name TEXT, role TEXT,
  can_approve INTEGER, is_helper INTEGER, contact TEXT);
CREATE TABLE IF NOT EXISTS cameras(id TEXT PRIMARY KEY, household_id TEXT, name TEXT, kind TEXT, ring_device_id TEXT,
  authorized INTEGER DEFAULT 0, authorized_at REAL);
CREATE TABLE IF NOT EXISTS calibrations(id TEXT PRIMARY KEY, household_id TEXT, camera_id TEXT, version TEXT,
  profile TEXT, reference_path TEXT, reference_source TEXT, created_at REAL, active INTEGER);
CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY, household_id TEXT, camera_id TEXT, media_source TEXT,
  event_source TEXT, event_id TEXT, captured_at REAL, received_at REAL, frame_sha256 TEXT, frame_path TEXT,
  annotated_path TEXT, classification TEXT, calibration_version TEXT, analysis TEXT, incident_id TEXT, disposition TEXT);
CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY, household_id TEXT, camera_id TEXT, state TEXT, opened_at REAL,
  updated_at REAL, origin_observation_id TEXT, latest_observation_id TEXT, latest_captured_at REAL,
  assistance_sent_at REAL, baseline_metric REAL, last_metric REAL, clear_streak INTEGER DEFAULT 0, resolution TEXT, closed_at REAL);
CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY, incident_id TEXT, household_id TEXT, kind TEXT, status TEXT,
  message TEXT, recipient_member_id TEXT, channel TEXT, drafted_by TEXT, approved_by TEXT, approved_at REAL,
  sent_at REAL, created_at REAL);
CREATE TABLE IF NOT EXISTS inbox(id TEXT PRIMARY KEY, household_id TEXT, member_id TEXT, channel TEXT, subject TEXT,
  body TEXT, incident_id TEXT, action_id TEXT, delivered_at REAL, read_at REAL);
CREATE TABLE IF NOT EXISTS events(event_key TEXT PRIMARY KEY, source TEXT, type TEXT, received_at REAL,
  body_sha256 TEXT, status TEXT, detail TEXT, household_id TEXT, observation_id TEXT);
CREATE TABLE IF NOT EXISTS graph_edges(id TEXT PRIMARY KEY, household_id TEXT, src_type TEXT, src_id TEXT, rel TEXT,
  dst_type TEXT, dst_id TEXT, observation_id TEXT, data TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS ledger(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, household_id TEXT, incident_id TEXT,
  kind TEXT, payload TEXT, prev_hash TEXT, entry_hash TEXT);
CREATE TABLE IF NOT EXISTS secrets(name TEXT PRIMARY KEY, value BLOB, updated_at REAL);
CREATE TABLE IF NOT EXISTS metrics(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, stage TEXT, ok INTEGER, latency_ms REAL, detail TEXT);
CREATE TABLE IF NOT EXISTS purge_guard(allow INTEGER);
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger WHEN (SELECT COALESCE(MAX(allow),0) FROM purge_guard) = 0
  BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE INDEX IF NOT EXISTS obs_hh ON observations(household_id, captured_at);
CREATE INDEX IF NOT EXISTS led_inc ON ledger(incident_id);
"""

GENESIS = "0" * 64


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha(s: str | bytes) -> str:
    return hashlib.sha256(s if isinstance(s, bytes) else s.encode()).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.conn.executescript(SCHEMA)
        self._migrate()

    MIGRATIONS = {
        "households": {"is_demo": "INTEGER DEFAULT 1"},
        "members": {"token_hash": "TEXT"},
        "observations": {"trust": "TEXT", "chronology": "TEXT", "asserted_captured_at": "REAL", "capture_session": "TEXT",
                         "evidence_origin": "TEXT", "capture_method": "TEXT"},
        "incidents": {"namespace": "TEXT", "calibration_version": "TEXT", "last_clear_session": "TEXT", "last_clear_at": "REAL",
                      "evidence_origin": "TEXT", "verification_basis": "TEXT"},
        "events": {"attempts": "INTEGER DEFAULT 0", "last_error": "TEXT", "updated_at": "REAL",
                   "payload": "BLOB", "owner": "TEXT", "lease_until": "REAL",
                   "disposition": "TEXT", "request_id": "TEXT", "device_id": "TEXT", "format": "TEXT"},
    }

    def _migrate(self):
        """Additive column migrations so existing data directories keep working."""
        for table, cols in self.MIGRATIONS.items():
            have = {r["name"] for r in self.all(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    self.x(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    def x(self, sql: str, params=()):
        with self.lock:
            return self.conn.execute(sql, params)

    def one(self, sql: str, params=()) -> dict | None:
        r = self.x(sql, params).fetchone()
        return dict(r) if r else None

    def all(self, sql: str, params=()) -> list[dict]:
        return [dict(r) for r in self.x(sql, params).fetchall()]

    def insert(self, table: str, row: dict):
        cols = ",".join(row)
        self.x(f"INSERT INTO {table}({cols}) VALUES({','.join('?' * len(row))})", tuple(row.values()))

    def update(self, table: str, key: str, kid, fields: dict):
        sets = ",".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE {table} SET {sets} WHERE {key}=?", (*fields.values(), kid))

    # ---- ledger -----------------------------------------------------------
    def append_ledger(self, household_id: str, kind: str, payload: dict, incident_id: str | None = None) -> dict:
        with self.lock:
            # one hash chain per household, so deleting a household's data never breaks another's chain
            last = self.one("SELECT entry_hash FROM ledger WHERE household_id=? ORDER BY seq DESC LIMIT 1", (household_id,))
            prev = last["entry_hash"] if last else GENESIS
            ts = time.time()
            body = canonical({"ts": ts, "household_id": household_id, "incident_id": incident_id, "kind": kind, "payload": payload})
            h = sha(prev + body)
            self.insert("ledger", {"ts": ts, "household_id": household_id, "incident_id": incident_id, "kind": kind,
                                   "payload": canonical(payload), "prev_hash": prev, "entry_hash": h})
            return {"kind": kind, "entry_hash": h, "prev_hash": prev, "ts": ts}

    def verify_ledger(self, household_id: str) -> dict:
        prev = GENESIS
        rows = self.all("SELECT * FROM ledger WHERE household_id=? ORDER BY seq", (household_id,))
        for r in rows:
            body = canonical({"ts": r["ts"], "household_id": r["household_id"], "incident_id": r["incident_id"],
                              "kind": r["kind"], "payload": json.loads(r["payload"])})
            if r["prev_hash"] != prev or sha(prev + body) != r["entry_hash"]:
                return {"intact": False, "broken_at_seq": r["seq"], "entries": len(rows)}
            prev = r["entry_hash"]
        return {"intact": True, "entries": len(rows), "head": prev}

    def purge_household(self, household_id: str):
        with self.lock:
            self.x("INSERT INTO purge_guard(allow) VALUES(1)")
            try:
                for t in ("ledger", "observations", "incidents", "actions", "inbox", "graph_edges", "calibrations", "cameras", "members", "events"):
                    self.x(f"DELETE FROM {t} WHERE household_id=?", (household_id,))
                self.x("DELETE FROM households WHERE id=?", (household_id,))
            finally:
                self.x("DELETE FROM purge_guard")
