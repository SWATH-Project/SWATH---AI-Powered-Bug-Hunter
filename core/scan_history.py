# core/scan_history.py
# Author         : Member 4
# Responsibility : Record scan metadata to a central database for the dashboard.
#                  Supports real-time progress updates for the live dashboard.
# ------------------------------------------------------------

import os
import json
import sqlite3
from datetime import datetime
from typing import Optional

class ScanHistory:
    def __init__(self, db_path: str = "~/.swath/history.db"):
        self.db_path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    status TEXT NOT NULL,
                    tag_count INTEGER,
                    output_dir TEXT,
                    current_phase TEXT,
                    current_tool TEXT,
                    tools_completed INTEGER DEFAULT 0,
                    tools_total INTEGER DEFAULT 0
                )
            ''')
            conn.commit()

            # Auto-migrate: add columns if they don't exist (for existing DBs)
            existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(scans)").fetchall()}
            migrations = {
                'current_phase': "ALTER TABLE scans ADD COLUMN current_phase TEXT",
                'current_tool': "ALTER TABLE scans ADD COLUMN current_tool TEXT",
                'tools_completed': "ALTER TABLE scans ADD COLUMN tools_completed INTEGER DEFAULT 0",
                'tools_total': "ALTER TABLE scans ADD COLUMN tools_total INTEGER DEFAULT 0",
            }
            for col, sql in migrations.items():
                if col not in existing_cols:
                    cursor.execute(sql)
            conn.commit()

    def record_start(self, domain: str, output_dir: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO scans (domain, start_time, status, output_dir) VALUES (?, ?, ?, ?)',
                (domain, datetime.utcnow().isoformat(), 'RUNNING', output_dir)
            )
            conn.commit()
            if cursor.lastrowid is None:
                raise RuntimeError("Failed to insert scan record")
            return cursor.lastrowid

    def record_end(self, scan_id: int, status: str, tag_count: int):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE scans SET end_time = ?, status = ?, tag_count = ?, current_phase = NULL, current_tool = NULL WHERE id = ?',
                (datetime.utcnow().isoformat(), status, tag_count, scan_id)
            )
            conn.commit()

    def update_progress(self, scan_id: int, phase: str = None, tool: str = None,
                        tools_completed: int = None, tools_total: int = None):
        """Update real-time progress for the live dashboard."""
        fields = []
        values = []
        if phase is not None:
            fields.append('current_phase = ?')
            values.append(phase)
        if tool is not None:
            fields.append('current_tool = ?')
            values.append(tool)
        if tools_completed is not None:
            fields.append('tools_completed = ?')
            values.append(tools_completed)
        if tools_total is not None:
            fields.append('tools_total = ?')
            values.append(tools_total)

        if not fields:
            return

        values.append(scan_id)
        sql = f"UPDATE scans SET {', '.join(fields)} WHERE id = ?"

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(sql, values)
            conn.commit()

    def get_recent(self, limit: int = 50) -> list:
        """Returns the most recent scans, newest first, up to `limit` rows."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM scans ORDER BY id DESC LIMIT ?',
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_scan(self, scan_id: int) -> Optional[dict]:
        """Returns a single scan record by ID, or None if not found."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM scans WHERE id = ?',
                (scan_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_active_scans(self) -> list:
        """Returns all currently running scans."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM scans WHERE status = 'RUNNING' ORDER BY id DESC")
            return [dict(row) for row in cursor.fetchall()]
