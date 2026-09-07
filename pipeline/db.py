"""ClickHouse bağlantısı. Ortam değişkenlerinden okuyor, kodda kimlik bilgisi yok.

Yerel geliştirme (Docker):
    docker run -d --name ch-dev -p 18123:8123 \
        -e CLICKHOUSE_PASSWORD=dev -e CLICKHOUSE_DB=cinema \
        clickhouse/clickhouse-server:25.3

ClickHouse Cloud:
    $env:CLICKHOUSE_HOST     = "xxx.clickhouse.cloud"
    $env:CLICKHOUSE_PORT     = "8443"
    $env:CLICKHOUSE_SECURE   = "1"
    $env:CLICKHOUSE_USER     = "default"
    $env:CLICKHOUSE_PASSWORD = "..."
    $env:CLICKHOUSE_DATABASE = "cinema"
"""

from __future__ import annotations

import os
from pathlib import Path


def config() -> dict:
    """Bağlantı ayarları. Varsayılanlar yerel Docker konteynerine bakıyor."""
    secure = os.environ.get("CLICKHOUSE_SECURE", "").lower() in ("1", "true", "yes")
    return {
        "host": os.environ.get("CLICKHOUSE_HOST", "localhost"),
        "port": int(os.environ.get("CLICKHOUSE_PORT", "8443" if secure else "18123")),
        "username": os.environ.get("CLICKHOUSE_USER", "default"),
        "password": os.environ.get("CLICKHOUSE_PASSWORD", "dev"),
        "database": os.environ.get("CLICKHOUSE_DATABASE", "cinema"),
        "secure": secure,
    }


def connect():
    """clickhouse-connect istemcisi döner.

    Track şartı: ClickHouse gerçekten import edilip çağrılıyor, README'de adı geçmiyor.
    """
    import clickhouse_connect

    settings = config()
    return clickhouse_connect.get_client(
        host=settings["host"],
        port=settings["port"],
        username=settings["username"],
        password=settings["password"],
        database=settings["database"],
        secure=settings["secure"],
    )


def describe() -> str:
    """Şifre olmadan bağlantı tarifi. Log'a basmak için güvenli."""
    settings = config()
    scheme = "https" if settings["secure"] else "http"
    return (
        f"{scheme}://{settings['username']}@{settings['host']}:{settings['port']}"
        f"/{settings['database']}"
    )


def create_table(client) -> None:
    """schema.sql'i uygular. DDL tek yerde duruyor, Python'da kopyası yok."""
    sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    for statement in sql.split(";"):
        # Yorum satırlarını at, kalan gerçek DDL ise çalıştır
        body = "\n".join(
            line for line in statement.splitlines() if not line.strip().startswith("--")
        ).strip()
        if body:
            client.command(body)
