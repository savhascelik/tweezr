"""Downloads a YouTube video and ingests it into the editing library.

    python -m dev.add_youtube "https://www.youtube.com/watch?v=..." --take-id YT_01 --speaker "Steve Jobs"
    python -m dev.add_youtube "https://youtu.be/..." --language tr --model small --max-duration 120
    python -m dev.add_youtube "https://youtube.com/shorts/..." --replace

Five automated steps:
  1. Download the YouTube video (720p/best MP4, up to max-duration seconds) to demo/media.
  2. Transcribe it to word-level timecodes using faster-whisper (CPU).
  3. Label the vocal delivery using Gemini (optional / skipped with --no-tone).
  4. Ingest word index and timecodes into ClickHouse.
  5. Save the take ingest document to demo/takes/<take_id>.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Allow running directly as `python dev/add_youtube.py` without -m
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeline import db, ingest, schema, tone, transcribe, youtube
from server import config


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Downloads a YouTube video and puts it into the library."
    )
    parser.add_argument("url", help="YouTube video or shorts URL")
    parser.add_argument(
        "--take-id",
        default="",
        help="unique take ID (e.g. YT_01). If omitted, derived from video title/ID.",
    )
    parser.add_argument("--scene", default="YT", help="scene tag (e.g. YT or SCENE1)")
    parser.add_argument("--camera", default="A", help="camera tag (e.g. A, B)")
    parser.add_argument("--speaker", default="", help="speaker name (e.g. MAYA, STEVE)")
    parser.add_argument(
        "--language",
        default="",
        help="ISO code such as tr, en, de. Empty means detect it from audio.",
    )
    parser.add_argument(
        "--model",
        default="base",
        help="Whisper model: base, small, medium, large-v3. small is better for non-English.",
    )
    parser.add_argument(
        "--max-duration",
        type=int,
        default=180,
        help="Maximum duration in seconds to download & transcribe (default: 180s = 3m).",
    )
    parser.add_argument(
        "--no-tone",
        action="store_true",
        help="skip the Gemini tone pass and write neutral (no API key needed)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="drop existing rows for this project first",
    )
    parser.add_argument("--project", default=config.DEMO_PROJECT)
    args = parser.parse_args()

    if not youtube.is_youtube_url(args.url):
        print(f"Error: {args.url!r} is not a valid YouTube URL.", file=sys.stderr)
        return 1

    # --- 1. Download YouTube video ---
    media_dir = config.MEDIA_DIR
    media_dir.mkdir(parents=True, exist_ok=True)

    print(f"1. Fetching info from YouTube: {args.url}")
    try:
        info = youtube.get_info(args.url)
        print(f"   Title   : {info['title']}")
        print(f"   Channel : {info['uploader']}")
        print(f"   Duration: {info['duration']:.1f}s (capping to max {args.max_duration}s)")
    except Exception as err:
        print(f"   Error fetching video info: {err}", file=sys.stderr)
        return 1

    video_id = info.get("id") or youtube.extract_video_id(args.url)
    take_id = (
        args.take_id
        or youtube.sanitize_label(f"YT_{video_id}", max_len=24)
    )
    speaker = args.speaker or (info.get("uploader") or "SPEAKER")[:32]

    print(f"\n2. Downloading media (take_id={take_id})...")
    try:
        downloaded_mp4, meta = youtube.download(
            args.url,
            media_dir,
            filename_stem=take_id,
            max_duration_s=args.max_duration,
        )
        size_mb = downloaded_mp4.stat().st_size / (1024 * 1024)
        print(f"   Saved -> {downloaded_mp4} ({size_mb:.1f} MB, {meta['duration']:.1f}s)")
    except Exception as err:
        print(f"   Download failed: {err}", file=sys.stderr)
        return 1

    # --- 2. Transcribe ---
    print(f"\n3. Transcribing with faster-whisper ({args.model})...")
    doc, stats = transcribe.transcribe(
        downloaded_mp4,
        take_id=take_id,
        project_id=args.project,
        scene=args.scene,
        camera=args.camera,
        speaker=speaker,
        source_url=downloaded_mp4.name,
        model_size=args.model,
        language=args.language or None,
    )

    for key in (
        "language",
        "language_probability",
        "language_source",
        "media_seconds",
        "realtime_factor",
        "lines",
    ):
        print(f"   {key:22} {stats[key]}")

    if not doc["takes"][0]["lines"]:
        print(
            "\nNo speech found in the downloaded video. Wrong language, or silent track?",
            file=sys.stderr,
        )
        return 1

    print("\n   Transcript Preview:")
    for line in doc["takes"][0]["lines"][:5]:
        first, last = line["words"][0]["start_ms"], line["words"][-1]["end_ms"]
        print(f"     [{first:>6} - {last:>6} ms]  {line['text']}")
    if len(doc["takes"][0]["lines"]) > 5:
        print(f"     ... and {len(doc['takes'][0]['lines']) - 5} more lines.")

    # --- 3. Delivery Tone ---
    print("\n4. Vocal Tone Analysis...")
    if args.no_tone:
        tone.apply_tones(doc, None, dry_run=True)
        print("   Tone analysis skipped (--no-tone). All lines labeled neutral.")
    elif not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        tone.apply_tones(doc, None, dry_run=True)
        print("   No GEMINI_API_KEY found. All lines labeled neutral.")
    else:
        try:
            tone.apply_tones(doc, downloaded_mp4, dry_run=False)
            print("   Gemini multimodal tone analysis complete.")
            for line in doc["takes"][0]["lines"][:3]:
                print(
                    f"     line={line['line_id']} {line['tone']:<8} "
                    f"({line['tone_score']:.2f})  {line.get('tone_reason', '')}"
                )
        except Exception as error:
            print(f"   Tone pass warning: {error}. Falling back to neutral.", file=sys.stderr)
            tone.apply_tones(doc, None, dry_run=True)

    # --- 4. Validation ---
    problems = schema.validate(doc)
    if problems:
        print("\nDocument validation failed:", file=sys.stderr)
        for p in problems:
            print(f"  ! {p}", file=sys.stderr)
        return 1

    # --- 5. Ingest into ClickHouse ---
    print(f"\n5. Ingesting into ClickHouse: {db.describe()}")
    try:
        client = db.connect()
        db.create_table(client)
    except Exception as error:
        print(f"   Could not connect to ClickHouse: {error}", file=sys.stderr)
        return 1

    if args.replace:
        client.command(
            "ALTER TABLE words DELETE WHERE project_id = {p:String}",
            parameters={"p": args.project},
        )
        print(f"   Cleared existing rows for project {args.project!r}")
    else:
        client.command(
            "ALTER TABLE words DELETE WHERE project_id = {p:String} AND take_id = {t:String}",
            parameters={"p": args.project, "t": take_id},
        )

    written = ingest.ingest(client, doc)
    print(f"   Successfully written {written} word rows to ClickHouse.")

    # Save take JSON document
    takes_dir = config.DEMO_TAKES_DIR
    takes_dir.mkdir(parents=True, exist_ok=True)
    doc_path = takes_dir / f"{take_id}.json"
    doc_path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"   Saved take definition -> {doc_path}")

    first_line = doc["takes"][0]["lines"][0]["text"]
    print("\n[OK] Done! In the web interface or via WebMCP:")
    print(f'   Search phrase : "{first_line[:50]}"')
    print(f"   Playable URL  : /media/{downloaded_mp4.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
