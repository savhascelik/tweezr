"""Ingest dokümanı -> ClickHouse `words` tablosu.

    python ingest.py ..\scratch\out.json
    python ingest.py fixture.json --replace

--replace aynı project_id'yi önce siliyor. Tekrar tekrar ingest edip aynı sonucu
almak istiyorsun, yoksa satırlar birikiyor ve arama iki kat sonuç veriyor.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import db
import queries
import schema


def ingest(client, doc: dict, replace: bool = False) -> int:
    """Dokümanı yazar, yazılan satır sayısını döner."""
    problems = schema.validate(doc)
    if problems:
        raise ValueError(
            "Doküman kontrata uymuyor, ingest yapılmadı:\n  "
            + "\n  ".join(problems)
        )

    rows = schema.flatten_rows(doc)
    if not rows:
        return 0

    if replace:
        client.command(
            queries.DROP_PROJECT, parameters={"project": doc["project_id"]}
        )

    client.insert("words", rows, column_names=schema.COLUMNS)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest dokümanını ClickHouse'a yazar.")
    parser.add_argument("docs", type=Path, nargs="+", help="bir veya daha fazla JSON")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="aynı project_id'nin mevcut satırlarını önce sil",
    )
    args = parser.parse_args()

    print(f"Bağlanıyor: {db.describe()}")
    try:
        client = db.connect()
    except Exception as error:  # bağlantı hatası kullanıcıya net dönsün
        print(f"\nClickHouse'a bağlanamadı: {error}", file=sys.stderr)
        print(
            "\nYerel geliştirme için:\n"
            "  docker run -d --name ch-dev -p 18123:8123 "
            "-e CLICKHOUSE_PASSWORD=dev -e CLICKHOUSE_DB=cinema "
            "clickhouse/clickhouse-server:25.3",
            file=sys.stderr,
        )
        return 1

    db.create_table(client)

    total = 0
    replace = args.replace
    for path in args.docs:
        doc = json.loads(path.read_text(encoding="utf-8"))
        written = ingest(client, doc, replace=replace)
        replace = False  # sadece ilk dokümanda sil, sonrakiler ekliyor
        total += written
        takes = ", ".join(t["take_id"] for t in doc["takes"])
        print(f"  {path.name:<24} {written:>6} satır  [{takes}]")

    print(f"\nToplam {total} satır yazıldı.")

    project = json.loads(args.docs[0].read_text(encoding="utf-8"))["project_id"]
    stats = client.query(queries.LIBRARY_STATS, parameters={"project": project})
    print(f"\nKütüphane ({project}):")
    for name, value in zip(stats.column_names, stats.result_rows[0]):
        print(f"  {name:22} {value}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
