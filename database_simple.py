import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Optional
from datetime import datetime, timedelta, timezone

def moscow_now():
    return datetime.utcnow() + timedelta(hours=3)

DB_PATH = "infra_diagnostic.db"


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                token TEXT UNIQUE NOT NULL,
                status TEXT DEFAULT 'offline',
                last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        try:
            conn.execute("ALTER TABLE agents ADD COLUMN group_name TEXT DEFAULT 'default'")
        except sqlite3.OperationalError:
            pass

        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                params TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                result TEXT,
                logs TEXT
            )
        """)
        conn.commit()

# соединение с БД
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def add_agent(name: str, token: str) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "INSERT INTO agents (name, token) VALUES (?, ?)",
            (name, token)
        )
        return cursor.lastrowid

def get_agent(agent_id: int) -> Optional[Dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return dict(row) if row else None

def get_agent_by_token(token: str) -> Optional[Dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM agents WHERE token = ?", (token,)
        ).fetchone()
        return dict(row) if row else None

def update_heartbeat(agent_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE agents SET status = 'online', last_heartbeat = ? WHERE id = ?",
            (moscow_now(), agent_id)
        )
        conn.commit()

def add_task(agent_id: int, task_type: str, params: dict) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "INSERT INTO tasks (agent_id, type, params, created_at) VALUES (?, ?, ?, ?)",
            (agent_id, task_type, json.dumps(params, ensure_ascii=False), moscow_now())
        )
        return cursor.lastrowid

def update_task_result(task_id: int, result: dict, logs: str, exit_code: int):
    with sqlite3.connect(DB_PATH) as conn:
        status = "done" if exit_code == 0 else "failed"
        conn.execute(
            """UPDATE tasks 
               SET status = ?, finished_at = ?, 
                   result = ?, logs = ?
               WHERE id = ?""",
            (status, moscow_now(), json.dumps(result, ensure_ascii=False), logs, task_id)
        )
        conn.commit()

def get_all_agents() -> List[Dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM agents ORDER BY id").fetchall()
        return [dict(row) for row in rows]

def get_recent_tasks(limit: int = 50) -> List[Dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

def get_task(task_id: int) -> Optional[Dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

def start_task(task_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE tasks SET status = 'running', started_at = ? WHERE id = ?",
            (moscow_now(), task_id)
        )
        conn.commit()

def increment_retry(task_id: int) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE tasks SET retry_count = retry_count + 1 WHERE id = ?",
            (task_id,)
        )
        conn.commit()
        row = conn.execute(
            "SELECT retry_count, max_retries FROM tasks WHERE id = ?",
            (task_id,)
        ).fetchone()
        return row[0] if row else 0
# длительность выполнения задачи
def get_task_duration(task: dict) -> str:
    if not task.get('started_at') or not task.get('finished_at'):
        return '-'

    # парсинг
    start_str = task['started_at']
    finish_str = task['finished_at']

    if isinstance(start_str, str):
        start = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
        finish = datetime.fromisoformat(finish_str.replace('Z', '+00:00'))
    else:
        start = start_str
        finish = finish_str

    duration = (finish - start).total_seconds()
    return f"{duration:.2f} сек"