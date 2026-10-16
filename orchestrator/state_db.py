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
                """
            )

    def create_task(self, title: str, instruction: str, branch_name: str) -> int:
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO tasks (title, instruction, branch_name, status, current_stage, created_at, updated_at)
                VALUES (?, ?, ?, 'IN_PROGRESS', 'EM_EVALUATION', ?, ?)
                """,
                (title, instruction, branch_name, now, now),
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
