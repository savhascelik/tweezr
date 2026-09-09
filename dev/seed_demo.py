"""Builds a playable demo corpus locally.

    python -m dev.seed_demo

Why it is needed: fixture.json is an example of the CONTRACT, it points at `gs://`
paths and those files do not exist. Developing the interface needs media that
actually plays.

What it does: synthesizes the same line in three different deliveries with Windows
SAPI, runs it through the real Whisper pipeline, writes it to ClickHouse and puts
the media under the demo media directory.

AN HONEST NOTE ABOUT THE TONE LABELS:
The tones here come from the synthesis settings (rate, volume), not from analysis.
So they are "set", not "measured". But the voices really are delivered differently,
which is why these labels serve as **ground truth** for the Gemini tone pass: when
pipeline.tone runs, it is expected to call the slow one "calm" and the fast one
"tense". If it does not, the problem is in the tone pass.
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

# (take_id, camera, SAPI rate, SAPI volume, tone label, what you will hear)
TAKES = [
    ("S01_T01", "A", 3, 100, "tense", "fast and loud"),
    ("S01_T03", "A", -2, 100, "calm", "slow and measured"),
    ("S01_T05", "B", -1, 30, "whisper", "slow and quiet"),
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
        print("This seed uses Windows SAPI. On another platform, drop in your own clips.")
        return 1

    media_dir = config.MEDIA_DIR
    media_dir.mkdir(parents=True, exist_ok=True)
    # Both of these are committed: they have to be present in the deployed image.
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
            # The server turns this into a /media/<file> URL
            source_url=wav.name,
        )

        # The tone comes from the synthesis setting, not from analysis. See the
        # module docstring.
        for line in doc["takes"][0]["lines"]:
            line["tone"] = tone
            line["tone_score"] = 0.9
            line["tone_reason"] = f"seed: SAPI rate={rate} volume={volume}"

        path = docs_dir / f"{take_id}.json"
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        documents.append(doc)

        transcript = " / ".join(l["text"] for l in doc["takes"][0]["lines"])
        print(f"   {stats['media_seconds']}s  rtf={stats['realtime_factor']}  {transcript}")

    print("\nWriting to ClickHouse...")
    client = db.connect()
    db.create_table(client)

    total = 0
    for index, doc in enumerate(documents):
        total += ingest.ingest(client, doc, replace=(index == 0))
    print(f"{total} rows")

    print("\nVerification: the same line should be found in three tones")
    from pipeline import search

    for match in search.phrase_search(client, config.DEMO_PROJECT, "I never asked for this"):
        print(
            f"   {match['take_id']}  cam={match['camera']}  tone={match['tone']:<8}"
            f"  [{match['start_ms']}-{match['end_ms']} ms]  {match['source_url']}"
        )

    print(f"\nMedia: {media_dir}")
    for item in sorted(media_dir.glob("*.wav")):
        print(f"   /media/{item.name}  ({item.stat().st_size // 1024} KB)")

    print(
        "\nTest the Gemini tone pass against this corpus -- the labels are ground truth:\n"
        "   S01_T01 tense (fast) / S01_T03 calm (slow) / S01_T05 whisper (quiet)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
