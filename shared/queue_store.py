#!/usr/bin/env python3
"""
Fila transacional (SQLite) para integrar Download -> Upload.

Melhorias sistêmicas incluídas:
- claim atômico com worker_id + heartbeat
- recuperação automática de jobs "processing" órfãos (timeout)
- backoff via available_at (evita retentar em loop)
- event log persistido (tabela events) para observabilidade por job
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "queue.db"

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  obra_nome TEXT NOT NULL,
  pasta TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,              -- queued | processing | done | failed
  tries INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,

  -- melhorias sistêmicas (podem ser adicionadas via migration)
  available_at INTEGER,              -- quando pode ser reprocessado (backoff)
  processing_started_at INTEGER,
  worker_id TEXT,
  heartbeat_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_available ON jobs(status, available_at);

CREATE TABLE IF NOT EXISTS runtime (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  ts INTEGER NOT NULL,
  level TEXT NOT NULL,
  message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_job_ts ON events(job_id, ts);
"""

@dataclass
class Job:
    id: str
    obra_nome: str
    pasta: str
    payload: Dict[str, Any]
    status: str
    tries: int
    last_error: Optional[str]
    created_at: int
    updated_at: int
    available_at: Optional[int] = None
    processing_started_at: Optional[int] = None
    worker_id: Optional[str] = None
    heartbeat_at: Optional[int] = None

def _now_ts() -> int:
    return int(time.time())

def _compute_backoff_seconds(tries: int) -> int:
    """
    Exponencial simples (1m, 2m, 4m, 8m...) com teto em 1h.
    tries começa em 1.
    """
    base = 60
    sec = base * (2 ** max(0, tries - 1))
    return int(min(sec, 3600))

class QueueStore:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        con = self._connect()
        try:
            con.executescript(SCHEMA_SQL)
            self._migrate(con)
        finally:
            con.close()

    def _migrate(self, con: sqlite3.Connection) -> None:
        """
        Migração leve: garante colunas novas em bases já existentes.
        (SQLite suporta ALTER TABLE ADD COLUMN.)
        """
        cols = {r["name"] for r in con.execute("PRAGMA table_info(jobs)").fetchall()}
        needed = {
            "available_at": "INTEGER",
            "processing_started_at": "INTEGER",
            "worker_id": "TEXT",
            "heartbeat_at": "INTEGER",
        }
        for c, typ in needed.items():
            if c not in cols:
                con.execute(f"ALTER TABLE jobs ADD COLUMN {c} {typ}")

        # events pode não existir em bases antigas (se SCHEMA_SQL foi mais antigo)
        con.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, ts INTEGER NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_events_job_ts ON events(job_id, ts)")

        # index para available_at
        con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_available ON jobs(status, available_at)")

    # ---------------------------
    # Runtime flags (start/stop)
    # ---------------------------
    def set_runtime(self, key: str, value: Any) -> None:
        ts = _now_ts()
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "INSERT INTO runtime(key, value_json, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), ts),
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def get_runtime(self, key: str, default: Any = None) -> Any:
        con = self._connect()
        try:
            row = con.execute("SELECT value_json FROM runtime WHERE key=?", (key,)).fetchone()
            if not row:
                return default
            return json.loads(row["value_json"])
        finally:
            con.close()

    # -------------
    # Event logging
    # -------------
    def log_event(self, job_id: Optional[str], level: str, message: str) -> None:
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO events(job_id, ts, level, message) VALUES(?,?,?,?)",
                (job_id, _now_ts(), level, message),
            )
        finally:
            con.close()

    def list_events(self, job_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT ts, level, message FROM events WHERE job_id=? ORDER BY ts DESC LIMIT ?",
                (job_id, int(limit)),
            ).fetchall()
            return [{"ts": r["ts"], "level": r["level"], "message": r["message"]} for r in rows][::-1]
        finally:
            con.close()

    # ---------------------------
    # Jobs (enqueue/claim/status)
    # ---------------------------
    def enqueue(self, job_id: str, obra_nome: str, pasta: str, payload: Dict[str, Any]) -> None:
        """
        Idempotente por job_id.
        - Se já está done, não mexe.
        - Caso contrário, atualiza e volta para queued.
        """
        ts = _now_ts()
        payload_json = json.dumps(payload, ensure_ascii=False)

        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row and row["status"] == "done":
                con.execute("COMMIT")
                return
            if row:
                con.execute(
                    "UPDATE jobs SET obra_nome=?, pasta=?, payload_json=?, status='queued', updated_at=?, available_at=NULL WHERE id=?",
                    (obra_nome, pasta, payload_json, ts, job_id),
                )
            else:
                con.execute(
                    "INSERT INTO jobs(id, obra_nome, pasta, payload_json, status, created_at, updated_at, available_at) "
                    "VALUES(?,?,?,?,?,?,?,NULL)",
                    (job_id, obra_nome, pasta, payload_json, "queued", ts, ts),
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def list_jobs(self, limit: int = 200) -> List[Job]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [self._row_to_job(r) for r in rows]
        finally:
            con.close()

    def reclaim_stale_processing(self, timeout_seconds: int = 600) -> int:
        """
        Devolve para queued jobs em processing com heartbeat muito antigo.
        Retorna quantos foram re-enfileirados.
        """
        now = _now_ts()
        cutoff = now - int(timeout_seconds)
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute(
                "SELECT id, tries FROM jobs WHERE status='processing' AND (heartbeat_at IS NULL OR heartbeat_at < ?)",
                (cutoff,),
            ).fetchall()
            n = 0
            for r in rows:
                jid = r["id"]
                # volta para queued com pequeno delay para evitar "pegar na hora" após crash
                con.execute(
                    "UPDATE jobs SET status='queued', worker_id=NULL, processing_started_at=NULL, heartbeat_at=NULL, "
                    "available_at=?, updated_at=?, last_error=COALESCE(last_error,'') || ? WHERE id=?",
                    (now + 30, now, f"\n[reclaim] processing órfão em {now}", jid),
                )
                n += 1
            con.execute("COMMIT")
            return n
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def claim_next(self, worker_id: Optional[str] = None) -> Optional[Job]:
        """
        Claim atômico:
        - pega o job mais antigo em queued cujo available_at é NULL ou <= now
        - marca processing e seta worker_id + processing_started_at + heartbeat_at
        """
        ts = _now_ts()
        wid = worker_id or str(uuid.uuid4())

        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM jobs "
                "WHERE status='queued' AND (available_at IS NULL OR available_at <= ?) "
                "ORDER BY created_at ASC LIMIT 1",
                (ts,),
            ).fetchone()
            if not row:
                con.execute("COMMIT")
                return None

            job_id = row["id"]
            con.execute(
                "UPDATE jobs SET status='processing', updated_at=?, processing_started_at=?, worker_id=?, heartbeat_at=? WHERE id=?",
                (ts, ts, wid, ts, job_id),
            )
            con.execute("COMMIT")
            row2 = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return self._row_to_job(row2)
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def heartbeat(self, job_id: str, worker_id: str) -> None:
        ts = _now_ts()
        con = self._connect()
        try:
            con.execute(
                "UPDATE jobs SET heartbeat_at=?, updated_at=? WHERE id=? AND worker_id=? AND status='processing'",
                (ts, ts, job_id, worker_id),
            )
        finally:
            con.close()

    def mark_done(self, job_id: str) -> None:
        ts = _now_ts()
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "UPDATE jobs SET status='done', updated_at=?, worker_id=NULL, heartbeat_at=NULL, processing_started_at=NULL WHERE id=?",
                (ts, job_id),
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def mark_failed(
        self,
        job_id: str,
        error: str,
        requeue: bool = True,
        max_tries: int = 5,
    ) -> Tuple[int, str]:
        """
        Marca failed e opcionalmente re-enfileira até max_tries.
        Inclui backoff via available_at.
        Retorna (tries_atual, status_final).
        """
        ts = _now_ts()
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT tries FROM jobs WHERE id=?", (job_id,)).fetchone()
            tries = (row["tries"] if row else 0) + 1

            status = "failed"
            available_at = None
            if requeue and tries < max_tries:
                status = "queued"
                available_at = ts + _compute_backoff_seconds(tries)

            con.execute(
                "UPDATE jobs SET tries=?, last_error=?, status=?, available_at=?, updated_at=?, worker_id=NULL, heartbeat_at=NULL, processing_started_at=NULL WHERE id=?",
                (tries, error, status, available_at, ts, job_id),
            )
            con.execute("COMMIT")
            return tries, status
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def _row_to_job(self, r: sqlite3.Row) -> Job:
        return Job(
            id=r["id"],
            obra_nome=r["obra_nome"],
            pasta=r["pasta"],
            payload=json.loads(r["payload_json"]),
            status=r["status"],
            tries=r["tries"],
            last_error=r["last_error"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            available_at=r["available_at"] if "available_at" in r.keys() else None,
            processing_started_at=r["processing_started_at"] if "processing_started_at" in r.keys() else None,
            worker_id=r["worker_id"] if "worker_id" in r.keys() else None,
            heartbeat_at=r["heartbeat_at"] if "heartbeat_at" in r.keys() else None,
        )


def mirror_legacy_queue_json(store: "QueueStore", fila_path):
    """Gera um espelho legacy da fila em JSON (compatibilidade/inspeção).

    A fonte de verdade é o SQLite. Este arquivo serve apenas para manter
    compatibilidade com fluxos antigos e facilitar inspeção manual.
    """
    try:
        from pathlib import Path
        p = Path(fila_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        jobs = store.list_jobs(limit=10000)
        fila = []
        for j in jobs:
            if j.status not in ("queued", "processing"):
                continue
            item = dict(j.payload or {})
            item.setdefault("obra_nome", j.obra_nome)
            item.setdefault("pasta", j.pasta)
            item.setdefault("job_id", j.id)
            fila.append(item)

        # escrita atômica
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(fila, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        # não deve quebrar o fluxo principal
        return

