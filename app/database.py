from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException

from app.config import DB_PATH


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS campaigns (
                id TEXT PRIMARY KEY,
                name TEXT,
                platform TEXT,
                repo_path TEXT,
                created_at TEXT,
                owner_id TEXT
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(campaigns)").fetchall()}
        if "owner_id" not in columns:
            conn.execute("ALTER TABLE campaigns ADD COLUMN owner_id TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS score_cache (
                cache_key TEXT PRIMARY KEY,
                scores_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def get_campaign(campaign_id: str, owner_id: Optional[str] = None) -> dict[str, Any]:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if owner_id:
            row = conn.execute(
                "SELECT id, name, platform, repo_path, created_at, owner_id FROM campaigns WHERE id = ? AND owner_id = ?",
                (campaign_id, owner_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, name, platform, repo_path, created_at, owner_id FROM campaigns WHERE id = ?",
                (campaign_id,),
            ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return dict(row)


def list_campaign_rows(owner_id: Optional[str] = None) -> list[dict[str, Any]]:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if owner_id:
            rows = conn.execute(
                "SELECT id, name, platform, repo_path, created_at, owner_id FROM campaigns WHERE owner_id = ? ORDER BY created_at DESC",
                (owner_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, platform, repo_path, created_at, owner_id FROM campaigns ORDER BY created_at DESC"
            ).fetchall()
    return [dict(row) for row in rows]


def insert_campaign(
    campaign_id: str,
    name: str,
    platform: str,
    repo_path: str,
    created_at: str,
    owner_id: str,
) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO campaigns (id, name, platform, repo_path, created_at, owner_id) VALUES (?, ?, ?, ?, ?, ?)",
            (campaign_id, name, platform, repo_path, created_at, owner_id),
        )
        conn.commit()


def _score_cache_key(campaign_id: str, content: str) -> str:
    normalised = re.sub(r"\s+", " ", content).strip()
    return hashlib.sha256(f"{campaign_id}:{normalised}".encode()).hexdigest()


def get_cached_score(campaign_id: str, content: str) -> Optional[dict[str, Any]]:
    key = _score_cache_key(campaign_id, content)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT scores_json FROM score_cache WHERE cache_key = ?", (key,)
        ).fetchone()
    if row:
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None
    return None


def set_cached_score(campaign_id: str, content: str, scores: dict[str, Any]) -> None:
    key = _score_cache_key(campaign_id, content)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO score_cache (cache_key, scores_json, created_at) VALUES (?, ?, ?)",
            (key, json.dumps(scores), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
