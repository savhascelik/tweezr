"""Ingest document -> the ClickHouse `words` table.

    python -m pipeline.ingest scratch\\out.json
    python -m pipeline.ingest pipeline\\fixture.json --replace

--replace deletes the same project_id first. You want ingesting twice to give the same
result; without it rows accumulate and search starts returning every match twice.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import db, embed, queries, schema


def ingest(
    client,
    doc: dict,
    replace: bool = False,
    compute_embeddings: bool = True,
) -> int:
    """Writes the document and returns how many word rows were written."""
    problems = schema.validate(doc)
    if problems:
        raise ValueError(
            "The document does not match the contract, nothing was ingested:\n  "
            + "\n  ".join(problems)
        )

    rows = schema.flatten_rows(doc)
    if not rows:
        return 0

    # Extract or compute embeddings for lines if available
    embeddings_map = {}
    if compute_embeddings and embed.available():
        for take in doc.get("takes", []):
            take_id = take["take_id"]
            take_embs = embed.embed_take_lines(take)
            for lid, vec in take_embs.items():
                embeddings_map[(take_id, lid)] = vec

    line_rows = schema.flatten_line_rows(doc, embeddings=embeddings_map)

    if replace:
        try:
            client.command(
                queries.DROP_PROJECT,
                parameters={"project": doc["project_id"]},
                settings={"mutations_sync": "1"},
            )
            client.command(
                queries.DROP_PROJECT_LINES,
                parameters={"project": doc["project_id"]},
                settings={"mutations_sync": "1"},
            )
        except Exception:
            client.command(
                queries.DROP_PROJECT, parameters={"project": doc["project_id"]}
            )
            client.command(
                queries.DROP_PROJECT_LINES, parameters={"project": doc["project_id"]}
            )

    client.insert("words", rows, column_names=schema.COLUMNS)
    if line_rows:
        client.insert("lines", line_rows, column_names=schema.LINE_COLUMNS)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Writes an ingest document into ClickHouse."
    )
    parser.add_argument("docs", type=Path, nargs="+", help="one or more JSON documents")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="delete this project_id's existing rows first",
    )
    args = parser.parse_args()

    print(f"Connecting: {db.describe()}")
    try:
        client = db.connect()
    except Exception as error:  # a connection failure should be legible
        print(f"\nCould not connect to ClickHouse: {error}", file=sys.stderr)
        print(
            "\nFor local development:\n"
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
        replace = False  # only the first document replaces; the rest append
        total += written
        takes = ", ".join(t["take_id"] for t in doc["takes"])
        print(f"  {path.name:<24} {written:>6} rows  [{takes}]")

    print(f"\n{total} rows written in total.")

    project = json.loads(args.docs[0].read_text(encoding="utf-8"))["project_id"]
    stats = client.query(queries.LIBRARY_STATS, parameters={"projects": [project]})
    print(f"\nLibrary ({project}):")
    for name, value in zip(stats.column_names, stats.result_rows[0]):
        print(f"  {name:22} {value}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
