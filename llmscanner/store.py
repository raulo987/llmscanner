"""Persistent storage (SQLite, stdlib only): saved hosts + all test results.

Data lives in ~/.llmscanner/llmscanner.db (override with $LLMSCANNER_HOME).
Connections are opened per call so the store is safe to use from both the GUI
thread and the asyncio worker thread.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

# Fields stored for a saved host profile (everything needed to reproduce a run).
HOST_FIELDS = [
    "name", "host", "port", "api_key", "endpoint", "model",
    "tokens", "ctx", "runs", "concurrency", "requests", "timeout",
]

# Which parameters actually affect each test type — used to decide whether a
# re-run counts as "the same config".
RELEVANT = {
    "sanity": [],
    "latency": ["tokens"],
    "throughput": ["tokens", "runs"],
    "load": ["tokens", "concurrency", "requests"],
    "context": ["ctx"],
    "sweep": ["tokens", "sweep_levels"],
    "prefix": ["ctx"],
    "determinism": ["tokens", "runs"],
    "limits": ["ctx", "ctx_probe"],
}


def data_dir() -> Path:
    base = os.environ.get("LLMSCANNER_HOME")
    p = Path(base) if base else (Path.home() / ".llmscanner")
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "llmscanner.db"


def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hosts (
            name TEXT PRIMARY KEY, host TEXT, port INTEGER, api_key TEXT,
            endpoint TEXT, model TEXT, tokens INTEGER, ctx INTEGER, runs INTEGER,
            concurrency INTEGER, requests INTEGER, timeout REAL, updated_at REAL)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, host TEXT, port INTEGER,
            model TEXT, endpoint TEXT, test_type TEXT, config_hash TEXT,
            summary TEXT, metrics_json TEXT, value REAL, value_label TEXT)"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_results_cfg ON results(config_hash)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS endpoints (
            host TEXT, port INTEGER, last_used REAL, uses INTEGER,
            PRIMARY KEY (host, port))"""
    )
    # Migrate databases created before the chart columns existed.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(results)")}
    if "value" not in cols:
        conn.execute("ALTER TABLE results ADD COLUMN value REAL")
    if "value_label" not in cols:
        conn.execute("ALTER TABLE results ADD COLUMN value_label TEXT")
    return conn


@contextlib.contextmanager
def _db():
    conn = _open()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------- hosts
def list_hosts() -> list[dict]:
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM hosts ORDER BY name")]


def save_host(profile: dict) -> None:
    row = {k: profile.get(k) for k in HOST_FIELDS}
    row["updated_at"] = time.time()
    cols = ", ".join(row.keys())
    ph = ", ".join("?" for _ in row)
    with _db() as c:
        c.execute(f"INSERT OR REPLACE INTO hosts ({cols}) VALUES ({ph})", list(row.values()))


def delete_host(name: str) -> None:
    with _db() as c:
        c.execute("DELETE FROM hosts WHERE name = ?", (name,))


# ----------------------------------------------------------------- results
def relevant_params(test_type: str, params: dict) -> dict:
    return {k: params.get(k) for k in RELEVANT.get(test_type, [])}


def config_hash(host: str, port, model, endpoint: str, test_type: str, params: dict) -> str:
    payload = json.dumps({
        "host": host,
        "port": int(port),
        "model": model or "",
        "endpoint": endpoint,
        "test": test_type,
        "params": relevant_params(test_type, params),
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def record_result(*, host, port, model, endpoint, test_type, cfg_hash,
                  summary: str, metrics: dict, value: float | None = None,
                  value_label: str = "", ts: float | None = None) -> None:
    ts = time.time() if ts is None else ts
    with _db() as c:
        c.execute(
            """INSERT INTO results
               (ts, host, port, model, endpoint, test_type, config_hash, summary,
                metrics_json, value, value_label)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, host, int(port), model or "", endpoint, test_type, cfg_hash,
             summary, json.dumps(metrics), value, value_label),
        )


def history_for(cfg_hash: str, limit: int = 50) -> list[dict]:
    """All past runs (newest first) that share the given config hash."""
    with _db() as c:
        rows = c.execute(
            "SELECT * FROM results WHERE config_hash = ? ORDER BY ts DESC LIMIT ?",
            (cfg_hash, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def all_results(limit: int = 2000) -> list[dict]:
    with _db() as c:
        rows = c.execute(
            "SELECT * FROM results ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def clear_results() -> None:
    with _db() as c:
        c.execute("DELETE FROM results")


# ----------------------------------------------------------------- endpoints
def record_endpoint(host: str, port) -> None:
    """Remember a host:port that was entered/used, for input autocomplete."""
    host = (host or "").strip()
    if not host:
        return
    try:
        port = int(port)
    except Exception:
        return
    with _db() as c:
        c.execute(
            """INSERT INTO endpoints (host, port, last_used, uses) VALUES (?, ?, ?, 1)
               ON CONFLICT(host, port)
               DO UPDATE SET last_used = excluded.last_used, uses = uses + 1""",
            (host, port, time.time()),
        )


def recent_hosts(limit: int = 50) -> list[str]:
    """Distinct hosts ever entered or benchmarked, most-recent first."""
    with _db() as c:
        rows = c.execute(
            """SELECT host, MAX(t) AS mt FROM (
                   SELECT host, last_used AS t FROM endpoints
                   UNION ALL SELECT host, ts AS t FROM results
               ) WHERE host IS NOT NULL AND host <> ''
               GROUP BY host ORDER BY mt DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [r["host"] for r in rows]


def recent_ports(host: str | None = None, limit: int = 20) -> list[str]:
    """Ports seen (optionally only for a given host), most-recent first."""
    with _db() as c:
        if host:
            rows = c.execute(
                """SELECT port, MAX(t) AS mt FROM (
                       SELECT host, port, last_used AS t FROM endpoints
                       UNION ALL SELECT host, port, ts AS t FROM results
                   ) WHERE host = ? GROUP BY port ORDER BY mt DESC LIMIT ?""",
                (host, limit),
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT port, MAX(t) AS mt FROM (
                       SELECT port, last_used AS t FROM endpoints
                       UNION ALL SELECT port, ts AS t FROM results
                   ) GROUP BY port ORDER BY mt DESC LIMIT ?""",
                (limit,),
            ).fetchall()
    return [str(r["port"]) for r in rows if r["port"] is not None]


def last_port_for(host: str):
    ports = recent_ports(host, limit=1)
    return int(ports[0]) if ports else None
