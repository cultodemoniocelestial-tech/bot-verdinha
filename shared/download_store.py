#!/usr/bin/env python3
"""
Fila transacional (SQLite) para jobs de DOWNLOAD.

Objetivos sistêmicos:
- Persistência: não perde jobs ao reiniciar
- Claim atômico: evita duplicação entre workers
- Backoff: available_at para não martelar falhas
- Heartbeat: recuperação de jobs "downloading" órfãos
- Progresso: campos simples no job para a UI (status endpoint)
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class DownloadJob:
    id: int
    job_id: str
    url: str
    nome: str
    pasta: str
    expected_total: int
    batch_size: int
    status: str  # queued, downloading, validating, done, failed
    tries: int
    available_at: int
    last_error: str
    result_json: str
    summary_json: str
    worker_id: str
    processing_started_at: int
    heartbeat_at: int
    chapter: int
    progress: int
    total_images: int
    state: str
    created_at: int
    updated_at: int


class DownloadQueueStore:
    def __init__(self, db_path: Optional[str] = None):
        root = Path(__file__).resolve().parent.parent
        self.db_path = str(Path(db_path) if db_path else (root / "data" / "queue.db"))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        return con

    def _ensure_schema(self) -> None:
        con = self._connect()
        try:
            con.execute("""
            CREATE TABLE IF NOT EXISTS download_jobs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT NOT NULL UNIQUE,
              url TEXT NOT NULL,
              nome TEXT NOT NULL,
              pasta TEXT NOT NULL,
              expected_total INTEGER NOT NULL DEFAULT 0,
              batch_size INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'queued',
              tries INTEGER NOT NULL DEFAULT 0,
              available_at INTEGER NOT NULL DEFAULT 0,
              last_error TEXT NOT NULL DEFAULT '',
              result_json TEXT NOT NULL DEFAULT '',
              summary_json TEXT NOT NULL DEFAULT '',
              worker_id TEXT NOT NULL DEFAULT '',
              processing_started_at INTEGER NOT NULL DEFAULT 0,
              heartbeat_at INTEGER NOT NULL DEFAULT 0,
              chapter INTEGER NOT NULL DEFAULT 0,
              progress INTEGER NOT NULL DEFAULT 0,
              total_images INTEGER NOT NULL DEFAULT 0,
              state TEXT NOT NULL DEFAULT 'idle',
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );
            """)
            con.execute("CREATE INDEX IF NOT EXISTS idx_download_status ON download_jobs(status);")
            con.execute("CREATE INDEX IF NOT EXISTS idx_download_available ON download_jobs(available_at);")

            # Flags simples (start/stop)
            con.execute("""
            CREATE TABLE IF NOT EXISTS flags (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at INTEGER NOT NULL
            );
            """)
        finally:
            con.close()

    # ----- Flags -----
    def set_flag(self, key: str, value: str) -> None:
        now = int(time.time())
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO flags(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, now),
            )
        finally:
            con.close()

    def get_flag(self, key: str, default: str = "0") -> str:
        con = self._connect()
        try:
            row = con.execute("SELECT value FROM flags WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default
        finally:
            con.close()

    # ----- Jobs -----
    def enqueue(self, url: str, nome: str, pasta: str, expected_total: int = 0, batch_size: int = 0, job_id: Optional[str] = None) -> str:
        now = int(time.time())
        jid = job_id or str(uuid.uuid4())
        con = self._connect()
        try:
            con.execute(
                """
                INSERT OR IGNORE INTO download_jobs(job_id,url,nome,pasta,expected_total,batch_size,status,tries,available_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?, 'queued', 0, ?, ?, ?)
                """,
                (jid, url, nome, pasta, int(expected_total or 0), int(batch_size or 0), now, now, now),
            )
        finally:
            con.close()
        return jid

    def list_jobs(self, limit: int = 200) -> List[DownloadJob]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT * FROM download_jobs ORDER BY created_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [self._row_to_job(r) for r in rows]
        finally:
            con.close()

    def count_status(self, status: str) -> int:
        """Conta jobs por status (ex.: queued, downloading, validating...)."""
        con = self._connect()
        try:
            row = con.execute(
                "SELECT COUNT(*) AS c FROM download_jobs WHERE status=?",
                (status,),
            ).fetchone()
            return int(row["c"]) if row else 0
        finally:
            con.close()

    def count_all(self) -> int:
        """Conta todos os jobs."""
        con = self._connect()
        try:
            row = con.execute("SELECT COUNT(*) AS c FROM download_jobs").fetchone()
            return int(row["c"]) if row else 0
        finally:
            con.close()

    def get_latest_active_job(self) -> Optional[DownloadJob]:
        """Retorna o job ativo mais recente (downloading/validating) baseado em updated_at."""
        con = self._connect()
        try:
            row = con.execute(
                """
                SELECT * FROM download_jobs
                WHERE status IN ('downloading','validating')
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
            return self._row_to_job(row) if row else None
        finally:
            con.close()

    def count_queued_ready(self) -> int:
        """Conta jobs queued que já podem rodar (available_at <= now)."""
        now = int(time.time())
        con = self._connect()
        try:
            row = con.execute(
                "SELECT COUNT(*) AS c FROM download_jobs WHERE status='queued' AND available_at <= ?",
                (now,),
            ).fetchone()
            return int(row["c"]) if row else 0
        finally:
            con.close()

    def get_job_by_job_id(self, job_id: str) -> Optional[DownloadJob]:
        con = self._connect()
        try:
            row = con.execute("SELECT * FROM download_jobs WHERE job_id=?", (job_id,)).fetchone()
            return self._row_to_job(row) if row else None
        finally:
            con.close()

    def claim_next(self, worker_id: str) -> Optional[DownloadJob]:
        now = int(time.time())
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE;")
            row = con.execute(
                """
                SELECT id FROM download_jobs
                WHERE status='queued' AND available_at <= ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if not row:
                con.execute("COMMIT;")
                return None
            job_row = con.execute(
                """
                UPDATE download_jobs
                SET status='downloading',
                    worker_id=?,
                    processing_started_at=?,
                    heartbeat_at=?,
                    state='starting',
                    updated_at=?
                WHERE id=? AND status='queued'
                RETURNING *
                """,
                (worker_id, now, now, now, row["id"]),
            ).fetchone()
            con.execute("COMMIT;")
            return self._row_to_job(job_row) if job_row else None
        except Exception:
            try:
                con.execute("ROLLBACK;")
            except Exception:
                pass
            raise
        finally:
            con.close()

    def heartbeat(self, job_id: str, chapter: int = 0, progress: int = 0, total_images: int = 0, state: str = "") -> None:
        now = int(time.time())
        con = self._connect()
        try:
            con.execute(
                """
                UPDATE download_jobs
                SET heartbeat_at=?,
                    chapter=?,
                    progress=?,
                    total_images=?,
                    state=CASE WHEN ?='' THEN state ELSE ? END,
                    updated_at=?
                WHERE job_id=? AND status IN ('downloading','validating')
                """,
                (now, int(chapter or 0), int(progress or 0), int(total_images or 0), state or "", state or "", now, job_id),
            )
        finally:
            con.close()

    def set_status(self, job_id: str, status: str, state: str = "") -> None:
        now = int(time.time())
        con = self._connect()
        try:
            con.execute(
                """
                UPDATE download_jobs
                SET status=?,
                    state=CASE WHEN ?='' THEN state ELSE ? END,
                    updated_at=?
                WHERE job_id=?
                """,
                (status, state or "", state or "", now, job_id),
            )
        finally:
            con.close()

    def mark_done(self, job_id: str, result: Dict[str, Any], summary: Dict[str, Any]) -> None:
        now = int(time.time())
        con = self._connect()
        try:
            con.execute(
                """
                UPDATE download_jobs
                SET status='done',
                    state='completed',
                    result_json=?,
                    summary_json=?,
                    updated_at=?
                WHERE job_id=?
                """,
                (json.dumps(result, ensure_ascii=False), json.dumps(summary, ensure_ascii=False), now, job_id),
            )
        finally:
            con.close()

    def mark_failed(self, job_id: str, error: str, next_available_at: int) -> None:
        now = int(time.time())
        con = self._connect()
        try:
            con.execute(
                """
                UPDATE download_jobs
                SET status='queued',
                    tries=tries+1,
                    last_error=?,
                    available_at=?,
                    state='error',
                    updated_at=?
                WHERE job_id=?
                """,
                (str(error)[:2000], int(next_available_at), now, job_id),
            )
        finally:
            con.close()

    def fail_permanently(self, job_id: str, error: str) -> None:
        now = int(time.time())
        con = self._connect()
        try:
            con.execute(
                """
                UPDATE download_jobs
                SET status='failed',
                    tries=tries+1,
                    last_error=?,
                    state='failed',
                    updated_at=?
                WHERE job_id=?
                """,
                (str(error)[:2000], now, job_id),
            )
        finally:
            con.close()

    def reclaim_stale_downloading(self, timeout_seconds: int = 600) -> int:
        """
        Devolve para queued jobs em downloading/validating com heartbeat muito antigo.
        """
        now = int(time.time())
        cutoff = now - int(timeout_seconds)
        con = self._connect()
        try:
            cur = con.execute(
                """
                UPDATE download_jobs
                SET status='queued',
                    worker_id='',
                    processing_started_at=0,
                    heartbeat_at=0,
                    state='reclaimed',
                    available_at=?,
                    updated_at=?
                WHERE status IN ('downloading','validating')
                  AND heartbeat_at > 0
                  AND heartbeat_at < ?
                """,
                (now + 5, now, cutoff),
            )
            return cur.rowcount or 0
        finally:
            con.close()

    def _row_to_job(self, r: sqlite3.Row) -> DownloadJob:
        return DownloadJob(**dict(r))
