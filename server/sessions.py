"""Anonim oturum ve kredi defteri.

Neden zorunlu giriş YOK: WebMCP araçları sayfa JavaScript'i tarafından kaydediliyor.
Jüri URL'yi ChatGPT in-app browser'da açtığında login duvarı görürse uygulama JS'i hiç
çalışmaz, registerTool çağrılmaz, ajan sıfır araç görür ve proje bozuk puanlanır.
Üstüne OAuth redirect akışları ajan güdümlü tarayıcıda kırılgan.

Spec'in lehimize olan tarafı: ajan tarayıcıdan oturum bağlamını devralıyor. Yani
session cookie'si ajanın çağrılarında da geçerli, ayrı bir token akışı kurmuyoruz.

Depo SQLite. Kredi düşürme tek atomik işlem olmak zorunda, yoksa iki eşzamanlı render
isteği aynı krediyi iki kere harcar.
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
    last_seen   TEXT NOT NULL
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
    """İstek başına bağlantı. WAL modu okuyucuyu yazıcıya bloke etmiyor."""
    config.SESSION_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.SESSION_DB, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        if write:
            # IMMEDIATE: yazma kilidini hemen al. Kredi düşürmede iki isteğin
            # aynı bakiyeyi okuyup ayrı ayrı harcamasını engelleyen şey bu.
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
    """Yeni anonim oturum. Kullanıcı hiçbir şey yapmıyor, ilk yüklemede oluşuyor."""
    credits = config.ROLE_CREDITS.get(role, config.GUEST_CREDITS)
    # token_urlsafe kriptografik olarak güvenli; oturum kimliği tahmin edilemez olmalı
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
            (session_id, credits, f"{role} açılış bakiyesi", stamp),
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
        super().__init__(f"{needed} kredi gerekiyor, bakiye {balance}")


def charge(session_id: str, amount: int, reason: str) -> int:
    """Krediyi atomik olarak düşürür, kalan bakiyeyi döner.

    amount 0 ise hiçbir şey yazmıyor — bedava işlemler deftere satır eklemesin,
    yoksa her arama defteri şişirir.
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


def history(session_id: str, limit: int = 20) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT delta, reason, at FROM ledger WHERE session_id = ?"
            " ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]
