"""Medya -> kelime bazlı zaman kodu. CPU'da çalışır.

Çıktı fixture.json ile AYNI şekilde. Ton alanı boş bırakılıyor; onu Gemini geçişi dolduruyor.

    python transcribe.py sample.wav --take-id S01_T01 --speaker MAYA --out out.json

Neden faster-whisper: bu bir hizalama işi, generative iş değil. Düz Whisper zaman kodlarını
cümle seviyesinde verir ve saniyelerce sapabilir; kelime seviyesi word_timestamps ile geliyor.
GPU sadece hız için — 3 dakika altı kliplerde CPU yetiyor.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import schema


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
    """Tek medya dosyasını ingest dokümanına çevirir. (doc, stats) döner."""
    from faster_whisper import WhisperModel

    load_started = time.perf_counter()
    # int8 CPU'da belirgin hızlanma sağlıyor, bu ölçekte doğruluk kaybı fark edilmiyor.
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    load_seconds = time.perf_counter() - load_started

    run_started = time.perf_counter()
    segments, info = model.transcribe(
        str(media),
        language=language,
        word_timestamps=True,   # kelime bazlı zaman kodunun geldiği yer
        vad_filter=True,        # sessizlikleri atar, zaman kodu kaymasını azaltır
        beam_size=5,
    )

    lines: list[dict] = []
    empty_segments = 0

    for index, segment in enumerate(segments, start=1):
        words = []
        for word in segment.words or []:
            text = word.word.strip()  # faster-whisper kelimeyi baştaki boşlukla veriyor
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
                "tone": None,        # Gemini geçişi dolduracak
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
        # 1.0'ın altı gerçek zamandan hızlı demek
        "realtime_factor": round(run_seconds / info.duration, 2) if info.duration else None,
        "lines": len(lines),
        "empty_segments": empty_segments,
    }

    return doc, stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Medyayı kelime bazlı zaman koduna çevirir.")
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
        print(f"Medya bulunamadı: {args.media}", file=sys.stderr)
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

    print("\n=== hizalama raporu ===")
    for key, value in schema.alignment_report(doc).items():
        print(f"  {key:22} {value}")

    problems = schema.validate(doc)
    if problems:
        print(f"\n=== {len(problems)} uyarı ===")
        for problem in problems:
            print(f"  ! {problem}")
    else:
        print("\nKontrat doğrulaması temiz.")

    print("\nTranskript:")
    for take in doc["takes"]:
        for line in take["lines"]:
            first = line["words"][0]["start_ms"]
            last = line["words"][-1]["end_ms"]
            print(f"  [{first:>6} - {last:>6} ms] {line['text']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
