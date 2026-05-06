from __future__ import annotations
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Optional


class ResponseCache:
    """Cache LLM responses to avoid redundant calls.

    Cache key: hash of (template_name, design_profile, parameters)
    Stored in SQLite in user config directory.
    TTL: 7 days.
    """

    TTL_SECONDS = 7 * 24 * 3600

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = Path.home() / ".kcad_auto_pcb" / "llm_cache.db"
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            "  key TEXT PRIMARY KEY,"
            "  response TEXT NOT NULL,"
            "  tokens INTEGER,"
            "  created REAL"
            ")"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_created ON cache(created)"
        )

    def _make_key(self, template: str, design_profile: dict, params: dict) -> str:
        """Create a deterministic cache key."""
        data = json.dumps({
            "template": template,
            "profile": design_profile,
            "params": params,
        }, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()

    def get(self, template: str, design_profile: dict, params: dict) -> Optional[str]:
        """Retrieve cached response if valid."""
        key = self._make_key(template, design_profile, params)
        row = self._conn.execute(
            "SELECT response, created FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if row:
            response, created = row
            if time.time() - created < self.TTL_SECONDS:
                return response
            # Expired
            self._conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            self._conn.commit()
        return None

    def set(self, template: str, design_profile: dict, params: dict,
            response: str, tokens: int = 0):
        """Store a response in cache."""
        key = self._make_key(template, design_profile, params)
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (key, response, tokens, created) "
            "VALUES (?, ?, ?, ?)",
            (key, response, tokens, time.time()),
        )
        self._conn.commit()
