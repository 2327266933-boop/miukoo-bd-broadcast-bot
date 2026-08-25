import json
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional


TERMINAL_RECIPIENT_STATUSES = ("replied", "completed", "cancelled", "failed")
TERMINAL_TASK_STATUSES = ("completed", "cancelled", "failed")


class SQLiteStore:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self._lock = threading.RLock()

    def init_db(self) -> None:
        directory = os.path.dirname(self.database_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    task_name TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_by TEXT,
                    created_at TEXT NOT NULL,
                    deadline_at TEXT,
                    follow_up_enabled INTEGER NOT NULL,
                    first_remind_after_minutes INTEGER NOT NULL,
                    remind_interval_minutes INTEGER NOT NULL,
                    max_remind_times INTEGER NOT NULL,
                    stop_when_replied INTEGER NOT NULL,
                    quiet_hours_start TEXT NOT NULL,
                    quiet_hours_end TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recipients (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    bd_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    contact_id TEXT NOT NULL,
                    group_name TEXT,
                    variables_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    send_count INTEGER NOT NULL DEFAULT 0,
                    reply_count INTEGER NOT NULL DEFAULT 0,
                    remind_count INTEGER NOT NULL DEFAULT 0,
                    last_sent_at TEXT,
                    last_replied_at TEXT,
                    next_remind_at TEXT,
                    failure_reason TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );

                CREATE INDEX IF NOT EXISTS idx_recipients_task_id
                    ON recipients(task_id);
                CREATE INDEX IF NOT EXISTS idx_recipients_next_remind_at
                    ON recipients(next_remind_at);
                CREATE INDEX IF NOT EXISTS idx_recipients_contact_id
                    ON recipients(contact_id);

                CREATE TABLE IF NOT EXISTS message_logs (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    bd_id TEXT NOT NULL,
                    message_kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    platform_message_id TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    sent_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id),
                    FOREIGN KEY(recipient_id) REFERENCES recipients(id)
                );

                CREATE INDEX IF NOT EXISTS idx_message_logs_task_id
                    ON message_logs(task_id);

                CREATE TABLE IF NOT EXISTS reply_logs (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    bd_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    platform_message_id TEXT,
                    received_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id),
                    FOREIGN KEY(recipient_id) REFERENCES recipients(id)
                );

                CREATE INDEX IF NOT EXISTS idx_reply_logs_task_id
                    ON reply_logs(task_id);
                """
            )

    def create_task(self, task: Dict[str, Any], recipients: List[Dict[str, Any]]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, task_name, channel, message_type, status, created_by,
                    created_at, deadline_at, follow_up_enabled,
                    first_remind_after_minutes, remind_interval_minutes,
                    max_remind_times, stop_when_replied, quiet_hours_start,
                    quiet_hours_end
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task["id"],
                    task["task_name"],
                    task["channel"],
                    task["message_type"],
                    task["status"],
                    task.get("created_by"),
                    task["created_at"],
                    task.get("deadline_at"),
                    1 if task["follow_up_enabled"] else 0,
                    task["first_remind_after_minutes"],
                    task["remind_interval_minutes"],
                    task["max_remind_times"],
                    1 if task["stop_when_replied"] else 0,
                    task["quiet_hours_start"],
                    task["quiet_hours_end"],
                ),
            )
            for recipient in recipients:
                conn.execute(
                    """
                    INSERT INTO recipients (
                        id, task_id, bd_id, name, contact_id, group_name,
                        variables_json, status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        recipient["id"],
                        recipient["task_id"],
                        recipient["bd_id"],
                        recipient["name"],
                        recipient["contact_id"],
                        recipient.get("group"),
                        json.dumps(recipient["variables"], ensure_ascii=False),
                        recipient["status"],
                    ),
                )

    def list_tasks(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    t.*,
                    COUNT(r.id) AS recipient_count,
                    SUM(CASE WHEN r.status = 'replied' THEN 1 ELSE 0 END) AS replied_count,
                    SUM(CASE WHEN r.status = 'failed' THEN 1 ELSE 0 END) AS failed_count
                FROM tasks t
                LEFT JOIN recipients r ON r.task_id = t.id
                GROUP BY t.id
                ORDER BY t.created_at DESC
                """
            ).fetchall()
        return [self._task_summary_from_row(row) for row in rows]

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            task_row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if task_row is None:
                return None

            recipient_rows = conn.execute(
                "SELECT * FROM recipients WHERE task_id = ? ORDER BY rowid",
                (task_id,),
            ).fetchall()
            message_rows = conn.execute(
                "SELECT * FROM message_logs WHERE task_id = ? ORDER BY sent_at",
                (task_id,),
            ).fetchall()
            reply_rows = conn.execute(
                "SELECT * FROM reply_logs WHERE task_id = ? ORDER BY received_at",
                (task_id,),
            ).fetchall()

        task = self._task_from_row(task_row)
        task["recipients"] = [self._recipient_from_row(row) for row in recipient_rows]
        task["message_logs"] = [dict(row) for row in message_rows]
        task["reply_logs"] = [dict(row) for row in reply_rows]
        return task

    def get_recipient(self, recipient_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM recipients WHERE id = ?",
                (recipient_id,),
            ).fetchone()
        if row is None:
            return None
        return self._recipient_from_row(row)

    def update_task_status(self, task_id: str, status: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = ? WHERE id = ?",
                (status, task_id),
            )

    def mark_recipient_sent(
        self,
        recipient_id: str,
        status: str,
        sent_at: str,
        next_remind_at: Optional[str],
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE recipients
                SET status = ?,
                    send_count = send_count + 1,
                    last_sent_at = ?,
                    next_remind_at = ?,
                    failure_reason = NULL
                WHERE id = ?
                """,
                (status, sent_at, next_remind_at, recipient_id),
            )

    def mark_recipient_followed_up(
        self,
        recipient_id: str,
        status: str,
        sent_at: str,
        next_remind_at: Optional[str],
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE recipients
                SET status = ?,
                    send_count = send_count + 1,
                    remind_count = remind_count + 1,
                    last_sent_at = ?,
                    next_remind_at = ?
                WHERE id = ?
                """,
                (status, sent_at, next_remind_at, recipient_id),
            )

    def reschedule_recipient(self, recipient_id: str, next_remind_at: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE recipients
                SET next_remind_at = ?
                WHERE id = ?
                """,
                (next_remind_at, recipient_id),
            )

    def mark_recipient_failed(self, recipient_id: str, reason: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE recipients
                SET status = 'failed',
                    failure_reason = ?,
                    next_remind_at = NULL
                WHERE id = ?
                """,
                (reason, recipient_id),
            )

    def stop_recipient(self, task_id: str, recipient_id: str) -> bool:
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM recipients WHERE task_id = ? AND id = ?",
                (task_id, recipient_id),
            ).fetchone()
            if existing is None:
                return False

            conn.execute(
                """
                UPDATE recipients
                SET status = 'cancelled',
                    next_remind_at = NULL
                WHERE task_id = ?
                  AND id = ?
                  AND status NOT IN ('replied', 'completed', 'failed')
                """,
                (task_id, recipient_id),
            )
            return True

    def count_sent_messages_to_contact(self, contact_id: str, since_iso: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM message_logs m
                JOIN recipients r ON r.id = m.recipient_id
                WHERE r.contact_id = ?
                  AND m.status = 'sent'
                  AND m.sent_at >= ?
                """,
                (contact_id, since_iso),
            ).fetchone()
        return int(row["count"] or 0)

    def log_message(self, message: Dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO message_logs (
                    id, task_id, recipient_id, bd_id, message_kind, content,
                    platform_message_id, status, error, sent_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message["id"],
                    message["task_id"],
                    message["recipient_id"],
                    message["bd_id"],
                    message["message_kind"],
                    message["content"],
                    message.get("platform_message_id"),
                    message["status"],
                    message.get("error"),
                    message["sent_at"],
                ),
            )

    def find_recipient_for_reply(
        self,
        task_id: Optional[str],
        bd_id: Optional[str],
        contact_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        clauses = []
        params = []

        if task_id:
            clauses.append("r.task_id = ?")
            params.append(task_id)
        if bd_id:
            clauses.append("r.bd_id = ?")
            params.append(bd_id)
        if contact_id:
            clauses.append("r.contact_id = ?")
            params.append(contact_id)

        if not clauses:
            return None

        query = """
            SELECT r.*
            FROM recipients r
            JOIN tasks t ON t.id = r.task_id
            WHERE {}
              AND t.status NOT IN ('cancelled', 'failed')
            ORDER BY t.created_at DESC
            LIMIT 1
        """.format(" AND ".join(clauses))

        with self._connect() as conn:
            row = conn.execute(query, tuple(params)).fetchone()
        if row is None:
            return None
        return self._recipient_from_row(row)

    def record_reply(
        self,
        reply_id: str,
        recipient_id: str,
        task_id: str,
        bd_id: str,
        content: str,
        platform_message_id: Optional[str],
        received_at: str,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reply_logs (
                    id, task_id, recipient_id, bd_id, content,
                    platform_message_id, received_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reply_id,
                    task_id,
                    recipient_id,
                    bd_id,
                    content,
                    platform_message_id,
                    received_at,
                ),
            )
            conn.execute(
                """
                UPDATE recipients
                SET status = 'replied',
                    reply_count = reply_count + 1,
                    last_replied_at = ?,
                    next_remind_at = NULL
                WHERE id = ?
                """,
                (received_at, recipient_id),
            )

    def get_due_recipients(self, now_iso: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    r.id AS recipient_id,
                    r.task_id,
                    r.bd_id,
                    r.name,
                    r.contact_id,
                    r.group_name,
                    r.variables_json,
                    r.status AS recipient_status,
                    r.send_count,
                    r.reply_count,
                    r.remind_count,
                    r.last_sent_at,
                    r.last_replied_at,
                    r.next_remind_at,
                    r.failure_reason,
                    t.channel,
                    t.message_type,
                    t.first_remind_after_minutes,
                    t.remind_interval_minutes,
                    t.max_remind_times,
                    t.quiet_hours_start,
                    t.quiet_hours_end
                FROM recipients r
                JOIN tasks t ON t.id = r.task_id
                WHERE t.status NOT IN ('completed', 'cancelled', 'failed')
                  AND t.follow_up_enabled = 1
                  AND r.status IN ('sent', 'waiting_follow_up', 'followed_up')
                  AND r.reply_count = 0
                  AND r.next_remind_at IS NOT NULL
                  AND r.next_remind_at <= ?
                  AND r.remind_count < t.max_remind_times
                ORDER BY r.next_remind_at ASC
                """,
                (now_iso,),
            ).fetchall()

        due = []
        for row in rows:
            item = dict(row)
            item["id"] = item["recipient_id"]
            item["variables"] = json.loads(item.pop("variables_json"))
            due.append(item)
        return due

    def refresh_task_status(self, task_id: str) -> None:
        with self._lock, self._connect() as conn:
            task = conn.execute(
                "SELECT status FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if task is None or task["status"] in TERMINAL_TASK_STATUSES:
                return

            active_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM recipients
                WHERE task_id = ?
                  AND status NOT IN ('replied', 'completed', 'cancelled', 'failed')
                """,
                (task_id,),
            ).fetchone()["count"]
            if active_count == 0:
                conn.execute(
                    "UPDATE tasks SET status = 'completed' WHERE id = ?",
                    (task_id,),
                )

    def cancel_task(self, task_id: str) -> bool:
        with self._lock, self._connect() as conn:
            task = conn.execute(
                "SELECT id FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if task is None:
                return False
            conn.execute(
                "UPDATE tasks SET status = 'cancelled' WHERE id = ?",
                (task_id,),
            )
            conn.execute(
                """
                UPDATE recipients
                SET status = 'cancelled',
                    next_remind_at = NULL
                WHERE task_id = ?
                  AND status NOT IN ('replied', 'completed', 'failed')
                """,
                (task_id,),
            )
            return True

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _task_summary_from_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        task = self._task_from_row(row)
        task["recipient_count"] = row["recipient_count"] or 0
        task["replied_count"] = row["replied_count"] or 0
        task["failed_count"] = row["failed_count"] or 0
        return task

    def _task_from_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        item["follow_up"] = {
            "enabled": bool(item.pop("follow_up_enabled")),
            "first_remind_after_minutes": item.pop("first_remind_after_minutes"),
            "remind_interval_minutes": item.pop("remind_interval_minutes"),
            "max_remind_times": item.pop("max_remind_times"),
            "stop_when_replied": bool(item.pop("stop_when_replied")),
            "quiet_hours": {
                "start": item.pop("quiet_hours_start"),
                "end": item.pop("quiet_hours_end"),
            },
        }
        return item

    def _recipient_from_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        item["group"] = item.pop("group_name")
        item["variables"] = json.loads(item.pop("variables_json"))
        return item
