import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class StateDB:
    """
    Penyimpanan ringan berbasis SQLite (WAL Mode) untuk mencatat:
    - Siklus hidup Task & Approval Gate CTO
    - Riwayat rotasi model gratis (Free LLM Router)
    - Log aktivitas Dynamic Agent Factory (Hire / Specialize / Retire)
    """

    def __init__(self, db_path: str | Path = "data/company_state.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    instruction TEXT NOT NULL,
                    branch_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    current_stage TEXT NOT NULL DEFAULT 'EM_INIT',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    summary TEXT DEFAULT '',
                    cto_feedback TEXT DEFAULT '',
                    pm_output TEXT DEFAULT '',
                    arch_output TEXT DEFAULT '',
                    dev_output TEXT DEFAULT '',
                    ops_output TEXT DEFAULT '',
                    commit_hash TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    details TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS llm_rotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_name TEXT NOT NULL,
                    failed_model TEXT NOT NULL,
                    selected_model TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    details TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    created_at REAL NOT NULL,
                    resolved_at REAL,
                    resolution_notes TEXT DEFAULT ''
                );
                """
            )
            # Migrasi tabel jika kolom baru belum ada di database lama
            cur = conn.execute("PRAGMA table_info(tasks)")
            columns = {row["name"] for row in cur.fetchall()}
            new_columns = {
                "pm_output": "TEXT DEFAULT ''",
                "arch_output": "TEXT DEFAULT ''",
                "dev_output": "TEXT DEFAULT ''",
                "ops_output": "TEXT DEFAULT ''",
                "commit_hash": "TEXT DEFAULT ''",
                "project_dir": "TEXT DEFAULT ''",
            }
            for col_name, col_def in new_columns.items():
                if col_name not in columns:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {col_name} {col_def}")

    def create_task(self, title: str, instruction: str, branch_name: str, project_dir: str = "") -> int:
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO tasks (title, instruction, branch_name, project_dir, status, current_stage, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'IN_PROGRESS', 'EM_EVALUATION', ?, ?)
                """,
                (title, instruction, branch_name, project_dir, now, now),
            )
            return int(cur.lastrowid)

    def update_task(
        self,
        task_id: int,
        *,
        status: Optional[str] = None,
        current_stage: Optional[str] = None,
        retry_count: Optional[int] = None,
        summary: Optional[str] = None,
        cto_feedback: Optional[str] = None,
        pm_output: Optional[str] = None,
        arch_output: Optional[str] = None,
        dev_output: Optional[str] = None,
        ops_output: Optional[str] = None,
        commit_hash: Optional[str] = None,
        project_dir: Optional[str] = None,
    ) -> None:
        fields: List[str] = ["updated_at = ?"]
        values: List[Any] = [time.time()]

        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if current_stage is not None:
            fields.append("current_stage = ?")
            values.append(current_stage)
        if retry_count is not None:
            fields.append("retry_count = ?")
            values.append(retry_count)
        if summary is not None:
            fields.append("summary = ?")
            values.append(summary)
        if cto_feedback is not None:
            fields.append("cto_feedback = ?")
            values.append(cto_feedback)
        if pm_output is not None:
            fields.append("pm_output = ?")
            values.append(pm_output)
        if arch_output is not None:
            fields.append("arch_output = ?")
            values.append(arch_output)
        if dev_output is not None:
            fields.append("dev_output = ?")
            values.append(dev_output)
        if ops_output is not None:
            fields.append("ops_output = ?")
            values.append(ops_output)
        if commit_hash is not None:
            fields.append("commit_hash = ?")
            values.append(commit_hash)
        if project_dir is not None:
            fields.append("project_dir = ?")
            values.append(project_dir)

        values.append(task_id)
        sql = f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?"
        with self._connect() as conn:
            conn.execute(sql, values)

    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row else None

    def list_pending_approvals(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = 'WAITING_CTO_APPROVAL' ORDER BY id ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_active_tasks(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tasks 
                WHERE status IN ('IN_PROGRESS', 'WAITING_CTO_APPROVAL', 'NEEDS_CTO_INTERVENTION', 'REJECTED_BY_CTO') 
                ORDER BY updated_at DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def get_recent_tasks(self, limit: int = 5) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def log_agent_event(self, action: str, agent_name: str, details: Dict[str, Any] | str) -> None:
        payload = details if isinstance(details, str) else json.dumps(details, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO agent_events (action, agent_name, details, created_at) VALUES (?, ?, ?, ?)",
                (action, agent_name, payload, time.time()),
            )

    def log_llm_rotation(
        self, agent_name: str, failed_model: str, selected_model: str, reason: str
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_rotations (agent_name, failed_model, selected_model, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (agent_name, failed_model, selected_model, reason, time.time()),
            )

    def get_recent_agent_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def create_incident(self, alert_type: str, severity: str, details: str) -> int:
        """Mencatat insiden anomali baru dari Watchdog."""
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO incidents (alert_type, severity, details, status, created_at)
                VALUES (?, ?, ?, 'OPEN', ?)
                """,
                (alert_type, severity, details, now),
            )
            return int(cur.lastrowid)

    def resolve_incident(self, incident_id: int, notes: str = "") -> None:
        """Menandai insiden telah diselesaikan oleh CTO atau auto-remediation."""
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE incidents
                SET status = 'RESOLVED', resolved_at = ?, resolution_notes = ?
                WHERE id = ?
                """,
                (now, notes, incident_id),
            )

    def get_open_incidents(self) -> List[Dict[str, Any]]:
        """Mengambil seluruh insiden yang belum terselesaikan."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM incidents WHERE status = 'OPEN' ORDER BY id DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_recent_incidents(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Mengambil riwayat insiden terbaru."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM incidents ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_last_incident_by_type(self, alert_type: str) -> Optional[Dict[str, Any]]:
        """Mengambil insiden terakhir untuk alert_type tertentu (untuk cooldown check)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM incidents WHERE alert_type = ? ORDER BY id DESC LIMIT 1",
                (alert_type,),
            ).fetchone()
            return dict(row) if row else None
