"""Yerelde oynatılabilir demo korpusu kurar.

    python -m dev.seed_demo

Neden gerekli: fixture.json bir KONTRAT örneği, `gs://` yollarına işaret ediyor ve o
dosyalar yok. Arayüzü geliştirmek için gerçekten çalan medya lazım.

Ne yapıyor: aynı repliği Windows SAPI ile üç farklı sunumda sentezliyor, gerçek Whisper
pipeline'ından geçiriyor, ClickHouse'a yazıyor ve medyayı scratch/media altına koyuyor.

TON ETİKETLERİ HAKKINDA DÜRÜST NOT:
Buradaki tonlar sentez ayarından geliyor (hız, ses seviyesi), analizden değil. Yani
"ölçülmüş" değil, "kurulmuş". Ama sesler GERÇEKTEN farklı sunuluyor, o yüzden bu
etiketler Gemini ton geçişi için **ground truth** işlevi görüyor: pipeline.tone
çalıştırıldığında yavaş olana "calm", hızlı olana "tense" demesi beklenir. Demezse
sorun ton geçişinde.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pipeline import db, ingest, transcribe
from server import config

LINE = "I never asked for this. Just let me go."

# (take_id, kamera, SAPI hızı, SAPI ses seviyesi, ton etiketi, ne duyulacak)
TAKES = [
    ("S01_T01", "A", 3, 100, "tense", "hızlı ve yüksek"),
    ("S01_T03", "A", -2, 100, "calm", "yavaş ve ölçülü"),
    ("S01_T05", "B", -1, 30, "whisper", "yavaş ve kısık"),
]

SPEAKER = "MAYA"
SCENE = "S01"

SYNTH = """
param([string]$Out, [int]$Rate, [int]$Volume, [string]$Text)
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $s.Rate = $Rate
    $s.Volume = $Volume
    $s.SetOutputToWaveFile($Out)
    $s.Speak($Text)
    $s.SetOutputToNull()
} finally { $s.Dispose() }
"""


def synthesize(out: Path, rate: int, volume: int, text: str) -> None:
    script = Path(tempfile.gettempdir()) / "cinema_synth.ps1"
    script.write_text(SYNTH, encoding="utf-8")
    subprocess.run(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(script),
            "-Out", str(out), "-Rate", str(rate), "-Volume", str(volume),
            "-Text", text,
        ],
        check=True,
        capture_output=True,
    )


def main() -> int:
    if sys.platform != "win32":
        print("Bu seed Windows SAPI kullanıyor. Başka platformda kendi kliplerini koy.")
        return 1

    media_dir = config.MEDIA_DIR
    media_dir.mkdir(parents=True, exist_ok=True)
    # Bu ikisi de commit ediliyor: dağıtılan imajda bulunmaları gerekiyor.
    docs_dir = config.DEMO_TAKES_DIR
    docs_dir.mkdir(parents=True, exist_ok=True)

    documents: list[dict] = []

    for take_id, camera, rate, volume, tone, description in TAKES:
        wav = media_dir / f"{take_id}.wav"
        print(f"{take_id}  ({description})")
        synthesize(wav, rate, volume, LINE)

        doc, stats = transcribe.transcribe(
            wav,
            take_id=take_id,
            project_id=config.DEMO_PROJECT,
            scene=SCENE,
            camera=camera,
            speaker=SPEAKER,
            # Sunucu bunu /media/<dosya> URL'ine çeviriyor
            source_url=wav.name,
        )

        # Ton sentez ayarından geliyor, analizden değil. Bkz. modül başlığı.
        for line in doc["takes"][0]["lines"]:
            line["tone"] = tone
            line["tone_score"] = 0.9
            line["tone_reason"] = f"seed: SAPI rate={rate} volume={volume}"

        path = docs_dir / f"{take_id}.json"
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        documents.append(doc)

        transcript = " / ".join(l["text"] for l in doc["takes"][0]["lines"])
        print(f"   {stats['media_seconds']}s  rtf={stats['realtime_factor']}  {transcript}")

    print("\nClickHouse'a yazılıyor...")
    client = db.connect()
    db.create_table(client)

    total = 0
    for index, doc in enumerate(documents):
        total += ingest.ingest(client, doc, replace=(index == 0))
    print(f"{total} satır")

    print("\nDoğrulama: aynı replik üç tonda bulunmalı")
    from pipeline import search

    for match in search.phrase_search(client, config.DEMO_PROJECT, "I never asked for this"):
        print(
            f"   {match['take_id']}  kam={match['camera']}  ton={match['tone']:<8}"
            f"  [{match['start_ms']}-{match['end_ms']} ms]  {match['source_url']}"
        )

    print(f"\nMedya: {media_dir}")
    for item in sorted(media_dir.glob("*.wav")):
        print(f"   /media/{item.name}  ({item.stat().st_size // 1024} KB)")

    print(
        "\nGemini ton geçişini bu korpusla sına — etiketler ground truth:\n"
        "   S01_T01 tense (hızlı) / S01_T03 calm (yavaş) / S01_T05 whisper (kısık)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
