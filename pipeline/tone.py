"""Ton sınıflandırması — Gemini multimodal.

Whisper'ın yapamadığı tek iş: "bu take diğerinden daha sakin". Kelime zaman kodu bir
hizalama problemi, ton ise prozodi + anlam problemi. İş bölümü bu yüzden.

Take başına TEK çağrı: ses + hizalı satır listesi -> satır başına ton etiketi.
8-12 take'lik bir korpus 8-12 çağrı demek. Tek yerde debug, öngörülebilir maliyet.

    $env:GEMINI_API_KEY = "..."
    python tone.py ..\scratch\out.json --media ..\scratch\sample.wav

    python tone.py ..\scratch\out.json --dry-run    # API anahtarı olmadan, hepsi neutral
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import schema

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
    """Sesi sıkıştırılmış mono kanala çıkarır.

    Hem video hem ses girdisini aynı yoldan geçiriyor ve istek boyutunu küçük tutuyor.
    Ham WAV'la 3 dakikalık klip birkaç MB, mp3 ile onda biri.
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
    """Bir take'i Gemini'ye gönderir. line_id -> {tone, tone_score, reason} döner."""
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY (veya GOOGLE_API_KEY) tanımlı değil.\n"
            "  $env:GEMINI_API_KEY = \"...\"\n"
            "Anahtarsız denemek için: --dry-run"
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
            temperature=0.0,  # etiketler koşular arasında oynamasın
        ),
    )

    audio.unlink(missing_ok=True)

    payload = json.loads(response.text)
    labels: dict[int, dict] = {}
    for item in payload.get("lines", []):
        tone = item.get("tone", "neutral")
        if tone not in schema.TONES:
            # Şema enum'u zorluyor ama modele güvenmiyoruz
            tone = "neutral"
        labels[int(item["line_id"])] = {
            "tone": tone,
            "tone_score": max(0.0, min(1.0, float(item.get("tone_score", 0.0)))),
            "reason": item.get("reason", ""),
        }
    return labels


def apply_tones(doc: dict, media: Path | None, dry_run: bool, model: str = MODEL) -> int:
    """Dokümandaki satırlara ton yazar. Etiketlenen satır sayısını döner."""
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
            raise ValueError("--media gerekiyor (ya da --dry-run)")

        labels = classify_take(take, media, model)

        missing = [l["line_id"] for l in take["lines"] if l["line_id"] not in labels]
        if missing:
            print(
                f"  uyarı: {take['take_id']} için {len(missing)} satır etiketlenmedi "
                f"(line_id {missing}), neutral yazılıyor"
            )

        for line in take["lines"]:
            label = labels.get(
                line["line_id"], {"tone": "neutral", "tone_score": 0.0, "reason": "eksik"}
            )
            line["tone"] = label["tone"]
            line["tone_score"] = label["tone_score"]
            line["tone_reason"] = label["reason"]
            labelled += 1

    return labelled


def main() -> int:
    parser = argparse.ArgumentParser(description="Satırlara Gemini ile ton etiketi yazar.")
    parser.add_argument("doc", type=Path, help="transcribe.py çıktısı")
    parser.add_argument("--media", type=Path, help="take'in sesi/videosu")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument(
        "--dry-run", action="store_true", help="API çağrısı yapmadan hepsini neutral yaz"
    )
    parser.add_argument("--out", type=Path, help="varsayılan: girdinin üstüne yaz")
    args = parser.parse_args()

    doc = json.loads(args.doc.read_text(encoding="utf-8"))

    try:
        labelled = apply_tones(doc, args.media, args.dry_run, args.model)
    except Exception as error:
        print(f"Ton geçişi başarısız: {error}", file=sys.stderr)
        return 1

    problems = schema.validate(doc)
    if problems:
        print("Ton yazıldıktan sonra kontrat bozuldu:", file=sys.stderr)
        for problem in problems:
            print(f"  ! {problem}", file=sys.stderr)
        return 1

    out = args.out or args.doc
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{labelled} satır etiketlendi -> {out}")

    print("\nEtiketler (gözle doğrula, bu ürünün farklılaştırıcı özelliği):")
    for take in doc["takes"]:
        for line in take["lines"]:
            reason = line.get("tone_reason", "")
            print(
                f"  {take['take_id']} satır={line['line_id']} "
                f"{line['tone']:<8} ({line['tone_score']:.2f})  "
                f"\"{line['text'][:48]}\"  {reason}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
