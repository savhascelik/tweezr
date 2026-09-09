"""Delivery classification — Gemini multimodal.

The one thing alignment cannot do: "this take is calmer than that one". Word timing is an
alignment problem; delivery is a prosody and meaning problem. Hence the split.

ONE call per take: audio plus the aligned line list -> a label per line.
A corpus of 8 to 12 takes means 8 to 12 calls. One place to debug, predictable cost.

    $env:GEMINI_API_KEY = "..."
    python -m pipeline.tone scratch\out.json --media scratch\sample.wav

    python -m pipeline.tone scratch\out.json --dry-run    # no API key needed; everything becomes neutral
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from . import schema

MODEL = "gemini-2.5-flash"

PROMPT = """You are a script supervisor logging takes for an editor.

You will hear one recorded take. Below are the lines spoken in it, with the exact
millisecond range where each line occurs in the audio.

For each line, classify HOW IT WAS DELIVERED — the vocal performance, not what the
words mean. Judge pace, volume, pitch movement, breath and tension.

Allowed tones:
  neutral  - flat, unremarkable delivery
  calm     - relaxed, measured, unhurried
  tense    - controlled but strained, held back
  angry    - raised, forceful, aggressive
  whisper  - breathy, very low volume
  shouted  - projected at full volume

tone_score is your confidence in the label, 0.0 to 1.0.
reason is one short clause naming the audible cue you used.

Important: judge only the audio in the given time range for each line. If a line's
delivery is unremarkable, say neutral rather than guessing something dramatic.

Lines in this take:
{lines}
"""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "lines": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "line_id": {"type": "INTEGER"},
                    "tone": {"type": "STRING", "enum": list(schema.TONES)},
                    "tone_score": {"type": "NUMBER"},
                    "reason": {"type": "STRING"},
                },
                "required": ["line_id", "tone", "tone_score"],
            },
        }
    },
    "required": ["lines"],
}


def extract_audio(media: Path) -> Path:
    """Extracts the audio as a compressed mono track.

    Puts video and audio input through the same path and keeps the request small. A three
    minute clip is several megabytes as raw WAV and a tenth of that as mp3.
    """
    import imageio_ffmpeg

    out = Path(tempfile.gettempdir()) / f"{media.stem}.tone.mp3"
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(media),
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(out),
        ],
        check=True,
    )
    return out


def describe_lines(take: dict) -> str:
    rows = []
    for line in take["lines"]:
        start = line["words"][0]["start_ms"]
        end = line["words"][-1]["end_ms"]
        rows.append(f'  line_id={line["line_id"]} [{start}-{end} ms] "{line["text"]}"')
    return "\n".join(rows)


def classify_take(take: dict, media: Path, model: str = MODEL) -> dict[int, dict]:
    """Sends one take to Gemini. Returns line_id -> {tone, tone_score, reason}."""
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY (or GOOGLE_API_KEY) is not set.\n"
            "  $env:GEMINI_API_KEY = \"...\"\n"
            "To try without a key: --dry-run"
        )

    audio = extract_audio(media)
    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(
                data=audio.read_bytes(), mime_type="audio/mpeg"
            ),
            PROMPT.format(lines=describe_lines(take)),
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
            temperature=0.0,  # keep labels stable across runs
        ),
    )

    audio.unlink(missing_ok=True)

    payload = json.loads(response.text)
    labels: dict[int, dict] = {}
    for item in payload.get("lines", []):
        tone = item.get("tone", "neutral")
        if tone not in schema.TONES:
            # The schema enforces an enum, but we do not take the model's word for it
            tone = "neutral"
        labels[int(item["line_id"])] = {
            "tone": tone,
            "tone_score": max(0.0, min(1.0, float(item.get("tone_score", 0.0)))),
            "reason": item.get("reason", ""),
        }
    return labels


def apply_tones(doc: dict, media: Path | None, dry_run: bool, model: str = MODEL) -> int:
    """Writes tone onto the document's lines. Returns how many were labelled."""
    labelled = 0

    for take in doc["takes"]:
        if dry_run:
            for line in take["lines"]:
                line["tone"] = "neutral"
                line["tone_score"] = 0.0
                line["tone_reason"] = "dry-run"
                labelled += 1
            continue

        if media is None:
            raise ValueError("--media is required (or use --dry-run)")

        labels = classify_take(take, media, model)

        missing = [l["line_id"] for l in take["lines"] if l["line_id"] not in labels]
        if missing:
            print(
                f"  warning: {len(missing)} lines in {take['take_id']} were not labelled "
                f"(line_id {missing}); writing neutral"
            )

        for line in take["lines"]:
            label = labels.get(
                line["line_id"], {"tone": "neutral", "tone_score": 0.0, "reason": "missing"}
            )
            line["tone"] = label["tone"]
            line["tone_score"] = label["tone_score"]
            line["tone_reason"] = label["reason"]
            labelled += 1

    return labelled


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Labels lines with a delivery tone using Gemini."
    )
    parser.add_argument("doc", type=Path, help="transcribe.py output")
    parser.add_argument("--media", type=Path, help="the take's audio or video")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument(
        "--dry-run", action="store_true", help="write neutral everywhere, no API call"
    )
    parser.add_argument("--out", type=Path, help="default: overwrite the input")
    args = parser.parse_args()

    doc = json.loads(args.doc.read_text(encoding="utf-8"))

    try:
        labelled = apply_tones(doc, args.media, args.dry_run, args.model)
    except Exception as error:
        print(f"The tone pass failed: {error}", file=sys.stderr)
        return 1

    problems = schema.validate(doc)
    if problems:
        print("The contract broke after writing tones:", file=sys.stderr)
        for problem in problems:
            print(f"  ! {problem}", file=sys.stderr)
        return 1

    out = args.out or args.doc
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{labelled} lines labelled -> {out}")

    print("\nLabels (check these yourself; this is the product's differentiator):")
    for take in doc["takes"]:
        for line in take["lines"]:
            reason = line.get("tone_reason", "")
            print(
                f"  {take['take_id']} line={line['line_id']} "
                f"{line['tone']:<8} ({line['tone_score']:.2f})  "
                f"\"{line['text'][:48]}\"  {reason}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
