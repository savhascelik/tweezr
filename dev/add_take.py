"""Puts one of your own recordings into the library. Audio or video, any language.

    python -m dev.add_take C:\\kayit\\take01.mp4 --take-id S02_T01 --speaker MAYA
    python -m dev.add_take take01.mp4 --take-id S02_T01 --language tr --model small
    python -m dev.add_take take01.mp4 --take-id S02_T01 --replace     # start a fresh library

Four steps that used to be four commands plus a manual file copy:

  1. Copy the media under demo/media, because that is the only directory /media serves
     and the only one the render path will resolve a file from.
  2. Transcribe it to word-level timecodes (faster-whisper, CPU).
  3. Label the delivery (Gemini). Skipped with --no-tone, which writes neutral —
     everything still works, you just lose the tone filter and the ranking.
  4. Write it to ClickHouse.

WHY THE MEDIA IS COPIED RATHER THAN REFERENCED
`source_url` is a bare filename on purpose. The server resolves it under MEDIA_DIR and
refuses anything that escapes, which is what stops a request from choosing which file
ffmpeg opens. A path pointing anywhere on your disk would have to defeat that check, so
the file comes to the library instead.

VIDEO WORKS THE SAME AS AUDIO
Nothing here special-cases it. Whisper reads the audio track through ffmpeg, the player
element is a <video>, and the render detects a video stream and produces mp4 instead of
wav. What you get from video that you do not get from audio: a real frame in the story
track thumbnails and a picture on the stage.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from pipeline import db, ingest, schema, tone, transcribe
from server import config

# Containers ffmpeg reads. Not a security boundary -- the file is one you chose -- just a
# way to fail on an obvious mistake before spending a minute on transcription.
KNOWN = {
    ".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma",
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Adds your own recording to the library.")
    parser.add_argument("media", type=Path, help="the audio or video file")
    parser.add_argument("--take-id", required=True, help="unique, e.g. S02_T01")
    parser.add_argument("--scene", default="", help="e.g. S02")
    parser.add_argument("--camera", default="", help="e.g. A")
    parser.add_argument("--speaker", default="", help="who is speaking")
    parser.add_argument(
        "--language",
        default="",
        help="ISO code such as tr, en, de. Empty means detect it from the audio.",
    )
    parser.add_argument(
        "--model",
        default="base",
        help="multilingual: base, small, medium, large-v3. small is better off English.",
    )
    parser.add_argument(
        "--no-tone",
        action="store_true",
        help="skip the Gemini pass and write neutral (no API key needed)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="drop the project's existing rows first, so this becomes the whole library",
    )
    parser.add_argument("--project", default=config.DEMO_PROJECT)
    args = parser.parse_args()

    if not args.media.is_file():
        print(f"Not found: {args.media}", file=sys.stderr)
        return 1

    suffix = args.media.suffix.lower()
    if suffix not in KNOWN:
        print(f"Unfamiliar extension {suffix!r}. Known: {' '.join(sorted(KNOWN))}", file=sys.stderr)
        return 1

    # --- 1. Into the library directory ---
    media_dir = config.MEDIA_DIR
    media_dir.mkdir(parents=True, exist_ok=True)
    stored = media_dir / f"{args.take_id}{suffix}"

    if stored.resolve() == args.media.resolve():
        print(f"Already in the library: {stored}")
    else:
        shutil.copy2(args.media, stored)
        size_mb = stored.stat().st_size / 1_048_576
        print(f"1. copied -> {stored}  ({size_mb:.1f} MB)")

    # --- 2. Word-level timecodes ---
    print(f"\n2. transcribing with {args.model}", end="")
    print(f", language {args.language}" if args.language else ", detecting the language")

    doc, stats = transcribe.transcribe(
        stored,
        take_id=args.take_id,
        project_id=args.project,
        scene=args.scene,
        camera=args.camera,
        speaker=args.speaker,
        # A bare filename: the server resolves it under MEDIA_DIR and refuses anything
        # that escapes. See the module docstring.
        source_url=stored.name,
        model_size=args.model,
        language=args.language or None,
    )

    for key in ("language", "language_probability", "language_source", "media_seconds", "realtime_factor", "lines"):
        print(f"     {key:22} {stats[key]}")

    if not doc["takes"][0]["lines"]:
        print("\nNo speech found. Wrong language, or the track is silent?", file=sys.stderr)
        return 1

    # A detection the model is unsure about is worth seeing before it becomes a library
    if stats["language_source"] == "detected" and stats["language_probability"] < 0.7:
        print(
            f"     WARNING: only {stats['language_probability']:.0%} sure of "
            f"{stats['language']!r}. Name it with --language if that looks wrong."
        )

    print("\n   transcript:")
    for line in doc["takes"][0]["lines"]:
        first, last = line["words"][0]["start_ms"], line["words"][-1]["end_ms"]
        print(f"     [{first:>7} - {last:>7} ms]  {line['text']}")

    # --- 3. Delivery ---
    print()
    if args.no_tone:
        tone.apply_tones(doc, None, dry_run=True)
        print("3. tone skipped, everything neutral (--no-tone)")
        print("   Search, picking, preview and render all work; the tone filter does not.")
    elif not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        tone.apply_tones(doc, None, dry_run=True)
        print("3. no GEMINI_API_KEY, everything neutral")
        print("   Set the key and run again to get the delivery labels.")
    else:
        print("3. labelling the delivery with Gemini")
        try:
            tone.apply_tones(doc, stored, dry_run=False)
            for line in doc["takes"][0]["lines"]:
                print(
                    f"     line={line['line_id']} {line['tone']:<8} "
                    f"({line['tone_score']:.2f})  {line.get('tone_reason', '')}"
                )
        except Exception as error:
            # A tone failure must not cost the transcription, which is the expensive part
            print(f"     tone pass failed: {error}", file=sys.stderr)
            print("     writing neutral and carrying on", file=sys.stderr)
            tone.apply_tones(doc, None, dry_run=True)

    # --- 4. Into ClickHouse ---
    problems = schema.validate(doc)
    if problems:
        print(f"\nThe document does not match the contract, nothing was written:", file=sys.stderr)
        for problem in problems:
            print(f"  ! {problem}", file=sys.stderr)
        return 1

    notes = schema.warnings(doc)
    if notes:
        # Shown, not fatal. These fire on ordinary footage; see pipeline/schema.py.
        print(f"\n   {len(notes)} notes on the alignment:")
        for note in notes:
            print(f"     - {note}")

    print(f"\n4. writing to ClickHouse: {db.describe()}")
    try:
        client = db.connect()
    except Exception as error:
        print(f"   Could not connect: {error}", file=sys.stderr)
        print("   docker compose -f dev/docker-compose.yml up -d", file=sys.stderr)
        return 1

    db.create_table(client)

    # Same take id twice would double every match, so the old rows go first.
    if args.replace:
        client.command("ALTER TABLE words DELETE WHERE project_id = {p:String}", parameters={"p": args.project})
        print(f"   dropped every row of project {args.project!r}")
    else:
        client.command(
            "ALTER TABLE words DELETE WHERE project_id = {p:String} AND take_id = {t:String}",
            parameters={"p": args.project, "t": args.take_id},
        )

    written = ingest.ingest(client, doc)
    print(f"   {written} rows")

    # The ingest document is kept next to the media so the take can be reloaded without
    # transcribing again -- that is what dev/load_demo.py reads.
    takes_dir = config.DEMO_TAKES_DIR
    takes_dir.mkdir(parents=True, exist_ok=True)
    doc_path = takes_dir / f"{args.take_id}.json"
    doc_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"   document -> {doc_path}")

    # --- What to try now ---
    first_line = doc["takes"][0]["lines"][0]["text"]
    print("\nDone. In the interface:")
    print(f'  search: "{first_line[:60]}"')
    print(f"  media : /media/{stored.name}")
    print("\nA phrase has to be an exact word sequence, so search words you can see in the")
    print("transcript above rather than what you remember saying.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
