"""Media -> word-level timecodes. Runs on CPU.

The output has the SAME shape as fixture.json. The tone field is left empty; the Gemini
pass fills it.

    python -m pipeline.transcribe scratch\\sample.wav --take-id S01_T01 --speaker MAYA --out scratch\\out.json

Why faster-whisper: this is alignment work, not generative work. Plain Whisper reports
timestamps at utterance level and can be off by seconds; word level arrives through
word_timestamps. GPU is only for speed — CPU is enough for clips under three minutes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import schema


def transcribe(
    media: Path,
    *,
    take_id: str,
    project_id: str = "demo",
    scene: str = "",
    camera: str = "",
    speaker: str = "",
    source_url: str = "",
    model_size: str = "base.en",
    language: str | None = "en",
) -> tuple[dict, dict]:
    """Turns one media file into an ingest document. Returns (doc, stats)."""
    from faster_whisper import WhisperModel

    load_started = time.perf_counter()
    # int8 is a clear speedup on CPU and the accuracy cost is not noticeable at this scale.
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    load_seconds = time.perf_counter() - load_started

    run_started = time.perf_counter()
    segments, info = model.transcribe(
        str(media),
        language=language,
        word_timestamps=True,   # where the word-level timing comes from
        vad_filter=True,        # drops silence, which reduces timestamp drift
        beam_size=5,
    )

    lines: list[dict] = []
    empty_segments = 0

    for index, segment in enumerate(segments, start=1):
        words = []
        for word in segment.words or []:
            text = word.word.strip()  # faster-whisper returns a leading space on words
            if not text:
                continue
            words.append(
                {
                    "word": text,
                    "start_ms": int(round(word.start * 1000)),
                    "end_ms": int(round(word.end * 1000)),
                    "confidence": round(float(word.probability), 4),
                }
            )

        if not words:
            empty_segments += 1
            continue

        lines.append(
            {
                "line_id": index,
                "text": segment.text.strip(),
                "tone": None,        # the Gemini pass fills this
                "tone_score": 0.0,
                "words": words,
            }
        )

    run_seconds = time.perf_counter() - run_started

    doc = {
        "project_id": project_id,
        "takes": [
            {
                "take_id": take_id,
                "scene": scene,
                "camera": camera,
                "speaker": speaker,
                "source_url": source_url or media.name,
                "lines": lines,
            }
        ],
    }

    stats = {
        "model": model_size,
        "media_seconds": round(info.duration, 2),
        "model_load_seconds": round(load_seconds, 2),
        "transcribe_seconds": round(run_seconds, 2),
        # Below 1.0 means faster than realtime
        "realtime_factor": round(run_seconds / info.duration, 2) if info.duration else None,
        "lines": len(lines),
        "empty_segments": empty_segments,
    }

    return doc, stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Turns media into word-level timecodes."
    )
    parser.add_argument("media", type=Path)
    parser.add_argument("--take-id", required=True)
    parser.add_argument("--project-id", default="demo")
    parser.add_argument("--scene", default="")
    parser.add_argument("--camera", default="")
    parser.add_argument("--speaker", default="")
    parser.add_argument("--source-url", default="")
    parser.add_argument("--model", default="base.en", help="tiny.en, base.en, small.en, medium ...")
    parser.add_argument("--language", default="en")
    parser.add_argument("--out", type=Path, default=Path("out.json"))
    args = parser.parse_args()

    if not args.media.exists():
        print(f"Media not found: {args.media}", file=sys.stderr)
        return 1

    doc, stats = transcribe(
        args.media,
        take_id=args.take_id,
        project_id=args.project_id,
        scene=args.scene,
        camera=args.camera,
        speaker=args.speaker,
        source_url=args.source_url,
        model_size=args.model,
        language=args.language or None,
    )

    args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n=== {args.media.name} -> {args.out} ===")
    for key, value in stats.items():
        print(f"  {key:22} {value}")

    print("\n=== alignment report ===")
    for key, value in schema.alignment_report(doc).items():
        print(f"  {key:22} {value}")

    problems = schema.validate(doc)
    if problems:
        print(f"\n=== {len(problems)} warnings ===")
        for problem in problems:
            print(f"  ! {problem}")
    else:
        print("\nContract validation clean.")

    print("\nTranscript:")
    for take in doc["takes"]:
        for line in take["lines"]:
            first = line["words"][0]["start_ms"]
            last = line["words"][-1]["end_ms"]
            print(f"  [{first:>6} - {last:>6} ms] {line['text']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
