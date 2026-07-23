import sqlite3
import os
import json
from loguru import logger
from datetime import datetime

class Database:
    """
    Persistent findings database for SWATH using SQLite.
    """
    
    def __init__(self, db_path="~/.swath/history.db"):
        self.db_path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        conn = self._get_conn()
        cur = conn.cursor()
        
        cur.executescript('''
        CREATE TABLE IF NOT EXISTS targets (
            id INTEGER PRIMARY KEY,
            domain TEXT UNIQUE NOT NULL,
            program TEXT,
            platform TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_scanned TIMESTAMP,
            scope_status TEXT,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY,
            target_id INTEGER REFERENCES targets(id),
            type TEXT NOT NULL,
            value TEXT NOT NULL,
            source TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP,
            is_alive BOOLEAN DEFAULT 1,
            metadata TEXT,
            UNIQUE(target_id, type, value)
        );

        CREATE TABLE IF NOT EXISTS findings (
            id INTEGER PRIMARY KEY,
            target_id INTEGER REFERENCES targets(id),
            asset_id INTEGER REFERENCES assets(id),
            scan_id INTEGER,
            severity TEXT NOT NULL,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            evidence TEXT,
            tool TEXT NOT NULL,
            template_id TEXT,
            is_verified BOOLEAN DEFAULT 0,
            is_false_positive BOOLEAN DEFAULT 0,
            is_reported BOOLEAN DEFAULT 0,
            platform TEXT,
            report_url TEXT,
            bounty_amount REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(target_id, type, title, tool)
        );

        CREATE TABLE IF NOT EXISTS asset_changes (
            id INTEGER PRIMARY KEY,
            target_id INTEGER REFERENCES targets(id),
            asset_id INTEGER REFERENCES assets(id),
            scan_id INTEGER,
            change_type TEXT NOT NULL,
            field_changed TEXT,
            old_value TEXT,
            new_value TEXT,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        ''')
        
        conn.commit()
        conn.close()

    def upsert_target(self, domain, program=None, platform=None):
        """Insert or update a target. Always returns the target ID, or None on error."""
        conn = self._get_conn()
        try:
            conn.execute('''
                INSERT INTO targets (domain, program, platform, last_scanned)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(domain) DO UPDATE SET
                    program=coalesce(?, program),
                    platform=coalesce(?, platform),
                    last_scanned=CURRENT_TIMESTAMP
            ''', (domain, program, platform, program, platform))
            conn.commit()
            
            cur = conn.execute('SELECT id FROM targets WHERE domain = ?', (domain,))
            row = cur.fetchone()
            return row['id'] if row else None
        except sqlite3.Error as e:
            logger.error(f"DB Error upserting target {domain}: {e}")
            return None
        finally:
            conn.close()
            
    def upsert_asset(self, target_id, asset_type, value, source, metadata=None):
        """Insert or update an asset. Always returns asset ID, or None on error."""
        if target_id is None:
            logger.warning(f"Cannot upsert asset '{value}': target_id is None")
            return None
        conn = self._get_conn()
        try:
            meta_str = json.dumps(metadata) if metadata else None
            conn.execute('''
                INSERT INTO assets (target_id, type, value, source, metadata, last_seen)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(target_id, type, value) DO UPDATE SET
                    last_seen=CURRENT_TIMESTAMP,
                    is_alive=1,
                    metadata=coalesce(?, metadata)
            ''', (target_id, asset_type, value, source, meta_str, meta_str))
            conn.commit()
            
            cur = conn.execute('SELECT id FROM assets WHERE target_id = ? AND type = ? AND value = ?', 
                               (target_id, asset_type, value))
            row = cur.fetchone()
            return row['id'] if row else None
        except sqlite3.Error as e:
            logger.error(f"DB Error upserting asset {value}: {e}")
            return None
        finally:
            conn.close()

    def add_finding(self, target_id, asset_id, scan_id, severity, finding_type, title, description=None, evidence=None, tool="unknown", template_id=None):
        """Insert or update a finding. Always returns finding ID, or None on error."""
        if target_id is None:
            logger.warning(f"Cannot add finding '{title}': target_id is None")
            return None
        conn = self._get_conn()
        try:
            conn.execute('''
                INSERT INTO findings (target_id, asset_id, scan_id, severity, type, title, description, evidence, tool, template_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_id, type, title, tool) DO UPDATE SET
                    updated_at=CURRENT_TIMESTAMP,
                    scan_id=?,
                    evidence=coalesce(?, evidence)
            ''', (target_id, asset_id, scan_id, severity, finding_type, title, description, evidence, tool, template_id, scan_id, evidence))
            conn.commit()
            
            cur = conn.execute('SELECT id FROM findings WHERE target_id = ? AND type = ? AND title = ? AND tool = ?', 
                               (target_id, finding_type, title, tool))
            row = cur.fetchone()
            return row['id'] if row else None
        except sqlite3.Error as e:
            logger.error(f"DB Error adding finding {title}: {e}")
            return None
        finally:
            conn.close()

    def get_findings(self, target_id=None, severity=None, is_reported=None):
        """Retrieve findings with optional filters. Returns list of dicts."""
        conn = self._get_conn()
        try:
            query = 'SELECT * FROM findings WHERE 1=1'
            params = []
            if target_id is not None:
                query += ' AND target_id = ?'
                params.append(target_id)
            if severity is not None:
                query += ' AND severity = ?'
                params.append(severity)
            if is_reported is not None:
                query += ' AND is_reported = ?'
                params.append(1 if is_reported else 0)
            cur = conn.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"DB Error getting findings: {e}")
            return []
        finally:
            conn.close()

    def get_assets(self, target_id, asset_type=None, is_alive=None):
        """Retrieve assets with optional filters. Returns list of dicts."""
        conn = self._get_conn()
        try:
            query = 'SELECT * FROM assets WHERE target_id = ?'
            params = [target_id]
            if asset_type is not None:
                query += ' AND type = ?'
                params.append(asset_type)
            if is_alive is not None:
                query += ' AND is_alive = ?'
                params.append(1 if is_alive else 0)
            cur = conn.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"DB Error getting assets: {e}")
            return []
        finally:
            conn.close()

    def get_stats(self):
        """Return summary statistics."""
        conn = self._get_conn()
        try:
            stats = {}
            cur = conn.execute('SELECT COUNT(*) as c FROM targets')
            stats['targets'] = cur.fetchone()['c']
            cur = conn.execute('SELECT COUNT(*) as c FROM assets')
            stats['assets'] = cur.fetchone()['c']
            cur = conn.execute('SELECT COUNT(*) as c FROM findings')
            stats['findings'] = cur.fetchone()['c']
            cur = conn.execute('SELECT severity, COUNT(*) as c FROM findings GROUP BY severity')
            stats['by_severity'] = {row['severity']: row['c'] for row in cur.fetchall()}
            return stats
        except sqlite3.Error as e:
            logger.error(f"DB Error getting stats: {e}")
            return {}
        finally:
            conn.close()
