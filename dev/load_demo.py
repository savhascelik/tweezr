"""Loads the demo corpus into ClickHouse.

    python -m dev.load_demo

Reads the ready-made ingest documents from `demo/takes/*.json` and writes them. It
does not run Whisper, does not call Gemini and does not want a GPU -- which is why
it also runs in the deployed environment without the transcription stack.

The split is deliberate: `seed_demo` PRODUCES the corpus (on a developer machine,
heavy), `load_demo` LOADS it (anywhere, light). The deployed instance runs the latter.
"""

from __future__ import annotations

import json
import sys

from pipeline import db, ingest, search
from server import config


def main() -> int:
    documents = sorted(config.DEMO_TAKES_DIR.glob("*.json"))
    if not documents:
        print(f"No documents in {config.DEMO_TAKES_DIR}.", file=sys.stderr)
        print("To produce the corpus: python -m dev.seed_demo", file=sys.stderr)
        return 1

    print(f"Connecting: {db.describe()}")
    try:
        client = db.connect()
    except Exception as error:
        print(f"Could not connect to ClickHouse: {error}", file=sys.stderr)
        return 1

    db.create_table(client)

    total = 0
    missing_media: list[str] = []
    for index, path in enumerate(documents):
        doc = json.loads(path.read_text(encoding="utf-8"))
        # Media presence is checked during load: a full database with missing files
        # would mean search works while playback does not.
        for take in doc["takes"]:
            name = take.get("source_url", "")
            if name and not (config.MEDIA_DIR / name).is_file():
                missing_media.append(f"{take['take_id']} -> {name}")

        written = ingest.ingest(client, doc, replace=(index == 0))
        total += written
        print(f"  {path.name:<20} {written:>5} rows")

    print(f"\n{total} rows written.")

    if missing_media:
        print(f"\nWARNING: media for {len(missing_media)} take(s) is missing under {config.MEDIA_DIR}:")
        for item in missing_media:
            print(f"  ! {item}")
        print("Search will work but playback and render will not.")

    matches = search.phrase_search(client, config.DEMO_PROJECT, "I never asked for this")
    if matches:
        print(f'\nVerification: "I never asked for this" -> {len(matches)} match(es)')
        for match in matches:
            print(f"  {match['take_id']:9} {match['tone']:8} [{match['start_ms']}-{match['end_ms']} ms]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
