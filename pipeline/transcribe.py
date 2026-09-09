"""Media -> word-level timecodes. Runs on CPU, in any language.

The output has the SAME shape as fixture.json. The tone field is left empty; the Gemini
pass fills it.

    python -m pipeline.transcribe scratch\\sample.wav --take-id S01_T01 --speaker MAYA --out scratch\\out.json
    python -m pipeline.transcribe kayit.mp4 --take-id S01_T01 --language tr

Why faster-whisper: this is alignment work, not generative work. Plain Whisper reports
timestamps at utterance level and can be off by seconds; word level arrives through
word_timestamps. GPU is only for speed — CPU is enough for clips under three minutes.

ON LANGUAGE
The default model is multilingual and the language is detected from the audio. That is a
deliberate default rather than a convenience: the `.en` models physically cannot
transcribe anything else, and picking one as the default would have made the whole
product English-only without ever saying so.

Naming a language with --language beats detection when you already know it, because
detection reads only the opening seconds and a quiet or musical intro can mislead it. It
also matters for orthography: told it is Turkish, Whisper writes "ışık" rather than
guessing at a spelling, and the search key depends on that.

For anything other than English, `small` is a noticeable step up from `base` and still
runs on CPU.
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
    model_size: str = "base",
    language: str | None = None,
) -> tuple[dict, dict]:
    """Turns one media file into an ingest document. Returns (doc, stats).

    `model_size` defaults to the multilingual `base`; `language=None` means detect it
    from the audio. Pass a language when you know it — detection only reads the opening
    seconds.
    """
    # Checked before the model loads, because loading one is slow and this is a mistake
    # worth refusing immediately. An .en model cannot transcribe anything else and does
    # not fail loudly about it: it returns confident nonsense in English.
    if model_size.endswith(".en") and language not in (None, "en"):
        raise ValueError(
            f"Model {model_size!r} is English-only but language={language!r} was asked "
            f"for. Use a multilingual model: base, small, medium, large-v3."
        )

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
            start_ms = int(round((word.start or 0.0) * 1000))
            end_ms = int(round((word.end or 0.0) * 1000))
            # faster-whisper can emit zero-length word timestamps for very fast tokens
            # (e.g. 26760 -> 26760). Ensure every word has a strictly positive duration.
            if end_ms <= start_ms:
                end_ms = start_ms + 40

            words.append(
                {
                    "word": text,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "confidence": round(float(word.probability or 0.0), 4),
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
        # What Whisper actually worked in. Reported whether it was named or detected,
        # because a wrong language is the difference between a transcript and noise, and
        # you want to see it before ingesting.
        "language": info.language,
        "language_probability": round(float(info.language_probability or 0), 3),
        "language_source": "given" if language else "detected",
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
    parser.add_argument(
        "--model",
        default="base",
        help="multilingual: base, small, medium, large-v3. English-only: base.en, small.en",
    )
    parser.add_argument(
        "--language",
        default="",
        help="ISO code such as tr, en, de. Empty means detect it from the audio.",
    )
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
        print(f"\n=== {len(problems)} contract problems ===")
        for problem in problems:
            print(f"  ! {problem}")
    else:
        print("\nContract validation clean.")

    notes = schema.warnings(doc)
    if notes:
        # Not problems. Printed because they are worth a look on real footage, and
        # deliberately not fatal because they fire on it routinely.
        print(f"\n=== {len(notes)} notes ===")
        for note in notes:
            print(f"  - {note}")

    print("\nTranscript:")
    for take in doc["takes"]:
        for line in take["lines"]:
            first = line["words"][0]["start_ms"]
            last = line["words"][-1]["end_ms"]
            print(f"  [{first:>6} - {last:>6} ms] {line['text']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
