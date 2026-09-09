"""The shared ClickHouse client.

Both the HTTP endpoints and the ADK agent's tools use the same client. Kept inside
routes.py it would force agent_tools.py to import that module, which is a circular
import.

On failure the client is dropped and rebuilt on the next call: ClickHouse Cloud closes
idle connections, and holding on to a dead client would fail every request after it.
"""

from __future__ import annotations

import threading

from pipeline import db

_client = None
_lock = threading.Lock()


def client():
    global _client
    with _lock:
        if _client is None:
            _client = db.connect()
        return _client


def drop() -> None:
    """Reconnect on the next call."""
    global _client
    with _lock:
        _client = None
