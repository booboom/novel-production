from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .models import WorkspacePaths
from .storage import atomic_write_text, sha256_text, utc_now

SCHEMA_VERSION = 1


def _connect(paths: WorkspacePaths) -> sqlite3.Connection:
    paths.database.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(paths.database, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_event_store(paths: WorkspacePaths) -> None:
    with _connect(paths) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              event_id TEXT NOT NULL UNIQUE,
              event_type TEXT NOT NULL,
              created_at TEXT NOT NULL,
              chapter INTEGER,
              payload_json TEXT NOT NULL,
              input_hash TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS projections (
              path TEXT PRIMARY KEY,
              content TEXT NOT NULL,
              content_hash TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              event_seq INTEGER NOT NULL,
              FOREIGN KEY(event_seq) REFERENCES events(seq)
            );
            CREATE TABLE IF NOT EXISTS llm_calls (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL,
              purpose TEXT NOT NULL,
              chapter INTEGER,
              route TEXT NOT NULL,
              provider TEXT NOT NULL,
              model TEXT NOT NULL,
              success INTEGER NOT NULL,
              latency_ms INTEGER NOT NULL DEFAULT 0,
              attempts INTEGER NOT NULL DEFAULT 1,
              input_hash TEXT NOT NULL,
              output_hash TEXT NOT NULL DEFAULT '',
              prompt_tokens INTEGER NOT NULL DEFAULT 0,
              completion_tokens INTEGER NOT NULL DEFAULT 0,
              total_tokens INTEGER NOT NULL DEFAULT 0,
              estimated_cost_usd REAL NOT NULL DEFAULT 0,
              repair_round INTEGER NOT NULL DEFAULT 0,
              error_type TEXT NOT NULL DEFAULT '',
              error_message TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS retrieval_chunks (
              chunk_id TEXT PRIMARY KEY,
              source_path TEXT NOT NULL,
              kind TEXT NOT NULL,
              chapter INTEGER,
              ordinal INTEGER NOT NULL,
              text TEXT NOT NULL,
              vector_json TEXT NOT NULL,
              content_hash TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type, seq);
            CREATE INDEX IF NOT EXISTS idx_events_chapter ON events(chapter, seq);
            CREATE INDEX IF NOT EXISTS idx_llm_calls_purpose ON llm_calls(purpose, created_at);
            CREATE INDEX IF NOT EXISTS idx_retrieval_source ON retrieval_chunks(source_path, ordinal);
            """
        )
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts USING fts5(chunk_id UNINDEXED, text, tokenize='unicode61')"
            )
        except sqlite3.OperationalError:
            # Some minimal SQLite builds omit FTS5. Vector/substring retrieval remains available.
            pass
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )


@contextmanager
def transaction(paths: WorkspacePaths) -> Iterator[sqlite3.Connection]:
    init_event_store(paths)
    conn = _connect(paths)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _safe_projection_path(paths: WorkspacePaths, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Invalid projection path: {relative_path}")
    target = (paths.root / relative).resolve()
    root = paths.root.resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"Projection escaped workspace: {relative_path}")
    return target


def commit_projection_event(
    paths: WorkspacePaths,
    *,
    event_type: str,
    payload: Mapping[str, Any],
    projections: Mapping[str, str],
    chapter: int | None = None,
    input_hash: str = "",
) -> dict[str, Any]:
    """Commit an event and all file projections in one SQLite transaction.

    SQLite is the durable source of truth. Files are materialized projections. If materialization
    is interrupted after the database commit, `rebuild_projections` deterministically repairs them.
    """
    event_id = str(uuid.uuid4())
    created_at = utc_now()
    normalized = {str(path): str(content) for path, content in projections.items()}
    with transaction(paths) as conn:
        cursor = conn.execute(
            "INSERT INTO events(event_id, event_type, created_at, chapter, payload_json, input_hash) VALUES(?,?,?,?,?,?)",
            (
                event_id,
                event_type,
                created_at,
                chapter,
                json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
                input_hash,
            ),
        )
        seq = int(cursor.lastrowid)
        for relative_path, content in normalized.items():
            _safe_projection_path(paths, relative_path)
            conn.execute(
                """
                INSERT INTO projections(path, content, content_hash, updated_at, event_seq)
                VALUES(?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                  content=excluded.content,
                  content_hash=excluded.content_hash,
                  updated_at=excluded.updated_at,
                  event_seq=excluded.event_seq
                """,
                (relative_path, content, sha256_text(content), created_at, seq),
            )

    materialized: list[str] = []
    pending: list[str] = []
    for relative_path, content in normalized.items():
        try:
            atomic_write_text(_safe_projection_path(paths, relative_path), content)
            materialized.append(relative_path)
        except Exception:
            pending.append(relative_path)
    return {
        "event_id": event_id,
        "event_seq": seq,
        "event_type": event_type,
        "materialized": materialized,
        "projection_pending": pending,
    }


def seed_projection(paths: WorkspacePaths, relative_path: str, content: str) -> None:
    init_event_store(paths)
    with transaction(paths) as conn:
        cursor = conn.execute(
            "INSERT INTO events(event_id, event_type, created_at, chapter, payload_json, input_hash) VALUES(?,?,?,?,?,?)",
            (str(uuid.uuid4()), "workspace.seed", utc_now(), None, "{}", ""),
        )
        seq = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT OR REPLACE INTO projections(path, content, content_hash, updated_at, event_seq)
            VALUES(?,?,?,?,?)
            """,
            (relative_path, content, sha256_text(content), utc_now(), seq),
        )


def seed_existing_workspace(paths: WorkspacePaths, *, include_prefixes: tuple[str, ...] | None = None) -> int:
    """Capture current text projections without rewriting them."""
    init_event_store(paths)
    candidates: list[Path] = []
    for path in paths.root.rglob("*"):
        if not path.is_file() or path == paths.database:
            continue
        relative = path.relative_to(paths.root).as_posix()
        if relative.startswith("checkpoints/") or relative.startswith(".locks/"):
            continue
        if include_prefixes and not any(relative.startswith(prefix) for prefix in include_prefixes):
            continue
        try:
            path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        candidates.append(path)
    if not candidates:
        return 0
    created_at = utc_now()
    with transaction(paths) as conn:
        cursor = conn.execute(
            "INSERT INTO events(event_id, event_type, created_at, chapter, payload_json, input_hash) VALUES(?,?,?,?,?,?)",
            (str(uuid.uuid4()), "workspace.seed_snapshot", created_at, None, json.dumps({"files": len(candidates)}), ""),
        )
        seq = int(cursor.lastrowid)
        for path in candidates:
            relative = path.relative_to(paths.root).as_posix()
            content = path.read_text(encoding="utf-8")
            conn.execute(
                """
                INSERT OR REPLACE INTO projections(path, content, content_hash, updated_at, event_seq)
                VALUES(?,?,?,?,?)
                """,
                (relative, content, sha256_text(content), created_at, seq),
            )
    return len(candidates)


def rebuild_projections(paths: WorkspacePaths, prefix: str = "") -> dict[str, Any]:
    init_event_store(paths)
    query = "SELECT path, content, content_hash FROM projections"
    params: tuple[Any, ...] = ()
    if prefix:
        query += " WHERE path LIKE ?"
        params = (f"{prefix}%",)
    query += " ORDER BY path"
    rebuilt: list[str] = []
    with _connect(paths) as conn:
        rows = conn.execute(query, params).fetchall()
    for row in rows:
        target = _safe_projection_path(paths, str(row["path"]))
        current_hash = ""
        if target.exists():
            try:
                current_hash = sha256_text(target.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, OSError):
                current_hash = ""
        if current_hash != row["content_hash"]:
            atomic_write_text(target, str(row["content"]))
            rebuilt.append(str(row["path"]))
    return {"rebuilt": rebuilt, "count": len(rebuilt), "scanned": len(rows)}


def list_events(paths: WorkspacePaths, limit: int = 100, event_type: str = "") -> list[dict[str, Any]]:
    init_event_store(paths)
    limit = max(1, min(1000, int(limit)))
    sql = "SELECT seq, event_id, event_type, created_at, chapter, payload_json, input_hash FROM events"
    params: list[Any] = []
    if event_type:
        sql += " WHERE event_type = ?"
        params.append(event_type)
    sql += " ORDER BY seq DESC LIMIT ?"
    params.append(limit)
    with _connect(paths) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [
        {
            "seq": int(row["seq"]),
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "created_at": row["created_at"],
            "chapter": row["chapter"],
            "payload": json.loads(row["payload_json"]),
            "input_hash": row["input_hash"],
        }
        for row in rows
    ]


def projection_status(paths: WorkspacePaths) -> dict[str, Any]:
    init_event_store(paths)
    drift: list[dict[str, str]] = []
    with _connect(paths) as conn:
        rows = conn.execute("SELECT path, content_hash FROM projections ORDER BY path").fetchall()
    for row in rows:
        target = _safe_projection_path(paths, str(row["path"]))
        if not target.exists():
            drift.append({"path": str(row["path"]), "status": "missing"})
            continue
        try:
            actual = sha256_text(target.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, OSError):
            drift.append({"path": str(row["path"]), "status": "unreadable"})
            continue
        if actual != row["content_hash"]:
            drift.append({"path": str(row["path"]), "status": "modified"})
    return {"ok": not drift, "tracked": len(rows), "drift": drift}


def open_connection(paths: WorkspacePaths) -> sqlite3.Connection:
    """Internal connection helper for observability and retrieval modules."""
    init_event_store(paths)
    return _connect(paths)
