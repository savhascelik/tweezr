"""Paylaşılan ClickHouse istemcisi.

Hem HTTP uçları hem ADK ajanının araçları aynı istemciyi kullanıyor. routes.py içinde
tutulsaydı agent_tools.py onu import etmek zorunda kalır ve döngüsel import çıkardı.

Hata olursa istemci düşürülüyor ve bir sonraki istekte yeniden kuruluyor: ClickHouse
Cloud bağlantıyı boşta bırakınca kapatıyor ve ölü bir istemciyi elde tutmak her isteği
düşürür.
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
    """Bir sonraki çağrıda yeniden bağlan."""
    global _client
    with _lock:
        _client = None
