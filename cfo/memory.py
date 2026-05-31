"""Durable state for the 24/7 runtime.

Two concerns, both backed by SQLite so they survive process restarts:

1. **Agent memory** (`agent_memory` table) — small key/value facts the agent
   chooses to persist across sessions ("user prefers FIFO", "watching NVDA").
   This is the cross-restart memory layer. Financial truth never lives here —
   it stays in `transactions`/`prices` and is always recomputed.

2. **Chat registry** (`chats` table) — which Telegram chats exist, whether they
   subscribe to the daily digest, and the last SDK `session_id` so a restarted
   bot can resume the same conversation thread instead of starting cold.
"""
from __future__ import annotations

from . import db

# ----- agent memory ---------------------------------------------------------

def remember(key: str, value: str, db_path=db.DB_PATH) -> None:
    """Store or overwrite a memory fact."""
    conn = db.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO agent_memory (key, value, updated_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=datetime('now')",
            (key.strip(), value),
        )
    conn.close()


def recall(key: str | None = None, db_path=db.DB_PATH) -> dict[str, str]:
    """Return one fact ({key: value}) or all facts when key is None."""
    conn = db.connect(db_path)
    if key is None:
        rows = conn.execute(
            "SELECT key, value FROM agent_memory ORDER BY key"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT key, value FROM agent_memory WHERE key = ?", (key.strip(),)
        ).fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def forget(key: str, db_path=db.DB_PATH) -> bool:
    """Delete a fact. Returns True if a row was removed."""
    conn = db.connect(db_path)
    with conn:
        cur = conn.execute("DELETE FROM agent_memory WHERE key = ?", (key.strip(),))
    n = cur.rowcount
    conn.close()
    return n > 0


# ----- runtime meta ---------------------------------------------------------

def get_meta(key: str, db_path=db.DB_PATH) -> str | None:
    conn = db.connect(db_path)
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def set_meta(key: str, value: str, db_path=db.DB_PATH) -> None:
    conn = db.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
    conn.close()


# ----- chat registry --------------------------------------------------------

def touch_chat(chat_id: int, db_path=db.DB_PATH) -> None:
    """Ensure a chat row exists (no-op if already present)."""
    conn = db.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO chats (chat_id) VALUES (?) ON CONFLICT(chat_id) DO NOTHING",
            (chat_id,),
        )
    conn.close()


def set_subscribed(chat_id: int, subscribed: bool, db_path=db.DB_PATH) -> None:
    conn = db.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO chats (chat_id, subscribed, updated_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(chat_id) DO UPDATE SET subscribed=excluded.subscribed, "
            "updated_at=datetime('now')",
            (chat_id, 1 if subscribed else 0),
        )
    conn.close()


def all_chats(db_path=db.DB_PATH) -> list[int]:
    """Every chat the bot has ever interacted with (for boot-time pre-warm)."""
    conn = db.connect(db_path)
    rows = conn.execute("SELECT chat_id FROM chats").fetchall()
    conn.close()
    return [r["chat_id"] for r in rows]


def subscribed_chats(db_path=db.DB_PATH) -> list[int]:
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT chat_id FROM chats WHERE subscribed = 1"
    ).fetchall()
    conn.close()
    return [r["chat_id"] for r in rows]


def get_session_id(chat_id: int, db_path=db.DB_PATH) -> str | None:
    conn = db.connect(db_path)
    row = conn.execute(
        "SELECT session_id FROM chats WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    conn.close()
    return row["session_id"] if row and row["session_id"] else None


def set_session_id(chat_id: int, session_id: str, db_path=db.DB_PATH) -> None:
    conn = db.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO chats (chat_id, session_id, updated_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(chat_id) DO UPDATE SET session_id=excluded.session_id, "
            "updated_at=datetime('now')",
            (chat_id, session_id),
        )
    conn.close()
