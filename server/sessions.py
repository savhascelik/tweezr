"""Anonymous sessions and the credit ledger.

Why there is NO login: WebMCP tools are registered by page JavaScript. If a judge opens
the URL in ChatGPT's in-app browser and meets a login wall, the application's JS never
runs, registerTool is never called, the agent sees zero tools, and the project gets
scored as broken. On top of that, OAuth redirect flows are fragile inside an
agent-driven browser.

The part of the spec that works in our favour: the agent inherits the session context
from the browser. The session cookie is therefore valid on the agent's calls too, and we
build no separate token flow.

Storage is SQLite. Deducting a credit has to be one atomic operation, or two concurrent
render requests spend the same credit twice.
"""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    role        TEXT NOT NULL DEFAULT 'guest',
    credits     INTEGER NOT NULL,
    ip          TEXT,
    created_at  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    -- The assistant is metered by a separate counter, NOT by credits: it is a
    -- different resource. Credits pay for render and ingest; an assistant turn is an
    -- LLM call, and leaving it free would publish an open LLM endpoint. Limiting by
    -- count also stops a judge running out of credits mid-demo.
    chat_used   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ledger (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    delta       INTEGER NOT NULL,
    reason      TEXT NOT NULL,
    at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ledger_session ON ledger (session_id, at);
CREATE INDEX IF NOT EXISTS sessions_ip ON sessions (ip, created_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection(write: bool = False):
    """One connection per request. WAL mode keeps readers from blocking on writers."""
    config.SESSION_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.SESSION_DB, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        if write:
            # IMMEDIATE takes the write lock straight away. This is what stops two
            # requests reading the same balance and each spending it.
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        if write:
            conn.execute("COMMIT")
    except Exception:
        if write:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.executescript(SCHEMA)
        # CREATE TABLE IF NOT EXISTS does not add columns to an existing table. A
        # database created earlier in development would be left without chat_used and
        # every assistant request would fail, so we make up for it here.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "chat_used" not in columns:
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN chat_used INTEGER NOT NULL DEFAULT 0"
            )


def as_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def recent_sessions_from_ip(ip: str, hours: int = 1) -> int:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with connection() as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM sessions WHERE ip = ? AND created_at >= ?",
            (ip, since),
        ).fetchone()
    return int(row["n"])


def create(ip: str = "", role: str = "guest") -> dict:
    """A new anonymous session, created on first load with the user doing nothing."""
    credits = config.ROLE_CREDITS.get(role, config.GUEST_CREDITS)
    # token_urlsafe is cryptographically secure; a session id must not be guessable
    session_id = secrets.token_urlsafe(32)
    stamp = now()

    with connection(write=True) as conn:
        conn.execute(
            "INSERT INTO sessions (id, role, credits, ip, created_at, last_seen)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, role, credits, ip, stamp, stamp),
        )
        conn.execute(
            "INSERT INTO ledger (session_id, delta, reason, at) VALUES (?, ?, ?, ?)",
            (session_id, credits, f"{role} opening balance", stamp),
        )

    return {
        "id": session_id,
        "role": role,
        "credits": credits,
        "ip": ip,
        "created_at": stamp,
        "last_seen": stamp,
    }


def get(session_id: str) -> dict | None:
    if not session_id:
        return None
    expiry = (
        datetime.now(timezone.utc) - timedelta(days=config.SESSION_TTL_DAYS)
    ).isoformat()
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ? AND last_seen >= ?",
            (session_id, expiry),
        ).fetchone()
    return as_dict(row)


def touch(session_id: str) -> None:
    with connection(write=True) as conn:
        conn.execute(
            "UPDATE sessions SET last_seen = ? WHERE id = ?", (now(), session_id)
        )


class InsufficientCredits(Exception):
    def __init__(self, needed: int, balance: int):
        self.needed = needed
        self.balance = balance
        super().__init__(f"{needed} credit needed, balance is {balance}")


def charge(session_id: str, amount: int, reason: str) -> int:
    """Deducts credit atomically and returns the remaining balance.

    With amount 0 it writes nothing: free operations should not add ledger rows, or
    every search would inflate the history.
    """
    if amount <= 0:
        session = get(session_id)
        return int(session["credits"]) if session else 0

    with connection(write=True) as conn:
        row = conn.execute(
            "SELECT credits FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise InsufficientCredits(amount, 0)

        balance = int(row["credits"])
        if balance < amount:
            raise InsufficientCredits(amount, balance)

        remaining = balance - amount
        conn.execute(
            "UPDATE sessions SET credits = ?, last_seen = ? WHERE id = ?",
            (remaining, now(), session_id),
        )
        conn.execute(
            "INSERT INTO ledger (session_id, delta, reason, at) VALUES (?, ?, ?, ?)",
            (session_id, -amount, reason, now()),
        )

    return remaining


def refund(session_id: str, amount: int, reason: str = "refund") -> int:
    """Gives credit back for work that was charged but never delivered.

    The counterpart to charge(), and it exists because an ingest can fail for reasons that
    are nobody's fault — a silent track, a container ffmpeg cannot read, the wrong
    language. Charging for that is indefensible, and the alternative of charging only on
    success would mean doing the work before knowing whether it can be paid for.

    A positive ledger row rather than a rewritten balance, so the history still shows what
    happened.
    """
    if amount <= 0:
        session = get(session_id)
        return int(session["credits"]) if session else 0

    with connection(write=True) as conn:
        row = conn.execute(
            "SELECT credits FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return 0

        restored = int(row["credits"]) + amount
        conn.execute(
            "UPDATE sessions SET credits = ?, last_seen = ? WHERE id = ?",
            (restored, now(), session_id),
        )
        conn.execute(
            "INSERT INTO ledger (session_id, delta, reason, at) VALUES (?, ?, ?, ?)",
            (session_id, amount, reason, now()),
        )

    return restored


class ChatLimitReached(Exception):
    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(f"Assistant limit reached for this session ({limit})")


def consume_chat(session_id: str, limit: int) -> int:
    """Increments the assistant counter atomically and returns what is left.

    Locked for the same reason as charge(): two concurrent messages reading the same
    counter and both passing would make the limit meaningless.
    """
    with connection(write=True) as conn:
        row = conn.execute(
            "SELECT chat_used FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise ChatLimitReached(limit)

        used = int(row["chat_used"])
        if used >= limit:
            raise ChatLimitReached(limit)

        conn.execute(
            "UPDATE sessions SET chat_used = ?, last_seen = ? WHERE id = ?",
            (used + 1, now(), session_id),
        )
    return limit - (used + 1)


def history(session_id: str, limit: int = 20) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT delta, reason, at FROM ledger WHERE session_id = ?"
            " ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]
