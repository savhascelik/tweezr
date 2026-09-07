"""Asıl test: kelime sınırından kesip DİNLEMEK.

Sayısal rapor hizalamanın makul olduğunu söyler, ama ürünün kalitesine kulak karar veriyor.
Kelimenin ortasından kesiyorsak kurgucu ilk oynatmada duyar.

    python verify_cut.py out.json --phrase "I never asked for this"          # nerede geçiyor
    python verify_cut.py out.json --phrase "..." --media sample.wav --cut 1  # tek parça
    python verify_cut.py out.json --phrase "..." --media sample.wav --splice # hepsi birleşik

--splice en önemlisi: ürünün gerçekte yaptığı şey bu. Birleşim noktaları temiz mi?

Buradaki cümle eşleştirme ClickHouse sorgusunun referans uygulaması: ardışık word_norm
eşleşmesi + start_ms sırası. SQL tarafı aynı sonucu vermek zorunda.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import schema


def find_phrase(doc: dict, phrase: str) -> list[dict]:
    """Cümlenin geçtiği yerleri bulur. Ardışık kelime eşleşmesi, satır içinde."""
    target = schema.normalize_phrase(phrase)
    if not target:
        return []

    matches: list[dict] = []

    for take in doc["takes"]:
        for line in take["lines"]:
            words = line["words"]
            norms = [schema.normalize_word(w["word"]) for w in words]

            for start_index in range(len(norms) - len(target) + 1):
                if norms[start_index : start_index + len(target)] != target:
                    continue
                span = words[start_index : start_index + len(target)]
                matches.append(
                    {
                        "take_id": take["take_id"],
                        "camera": take.get("camera", ""),
                        "speaker": take.get("speaker", ""),
                        "line_id": line["line_id"],
                        "tone": line.get("tone"),
                        "tone_score": line.get("tone_score", 0.0),
                        "start_ms": span[0]["start_ms"],
                        "end_ms": span[-1]["end_ms"],
                        "words": span,
                    }
                )

    matches.sort(key=lambda m: (m["take_id"], m["start_ms"]))
    return matches


def assemble_words(doc: dict, phrase: str) -> tuple[list[dict], list[str]]:
    """Kütüphanedeki tek tek kelimelerden yeni bir cümle kurar.

    Ürünün en zor testi ve en çok iddia ettiği şey: kaynakta yan yana olmayan kelimeleri
    birleştirmek. Hizalama bozuksa burada duyulur.

    (seçilen kelimeler, kütüphanede olmayanlar) döner.
    """
    index: dict[str, list[dict]] = {}
    for take in doc["takes"]:
        for line in take["lines"]:
            for word in line["words"]:
                norm = schema.normalize_word(word["word"])
                if not norm:
                    continue
                index.setdefault(norm, []).append(
                    {
                        **word,
                        "take_id": take["take_id"],
                        "line_id": line["line_id"],
                        "norm": norm,
                    }
                )

    chosen: list[dict] = []
    missing: list[str] = []
    for target in schema.normalize_phrase(phrase):
        options = index.get(target)
        if not options:
            missing.append(target)
            continue
        # En yüksek güvenli örneği al
        chosen.append(max(options, key=lambda w: w.get("confidence", 0.0)))

    return chosen, missing


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def cut(media: Path, start_ms: int, end_ms: int, out: Path, pad_ms: int = 0) -> None:
    """Verilen aralığı keser. Yeniden encode ediyor — kesim noktası örnek hassasiyetinde olsun."""
    start = max(0, start_ms - pad_ms) / 1000
    duration = (end_ms + pad_ms - max(0, start_ms - pad_ms)) / 1000

    command = [
        ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(media),
    ]
    if out.suffix.lower() == ".wav":
        command += ["-c:a", "pcm_s16le", "-vn"]
    subprocess.run(command + [str(out)], check=True)


def splice(media: Path, spans: list[tuple[int, int]], out: Path, pad_ms: int = 0) -> None:
    """Parçaları kesip birleştirir. Ürünün gerçekte yaptığı şey."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        parts: list[Path] = []
        for index, (start_ms, end_ms) in enumerate(spans):
            part = tmpdir / f"part{index:02d}.wav"
            cut(media, start_ms, end_ms, part, pad_ms)
            parts.append(part)

        listing = tmpdir / "parts.txt"
        listing.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8"
        )
        subprocess.run(
            [
                ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(listing),
                "-c:a", "pcm_s16le", str(out),
            ],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Kelime sınırından kesim doğrulaması.")
    parser.add_argument("doc", type=Path, help="transcribe.py çıktısı ya da fixture.json")
    parser.add_argument("--phrase", required=True)
    parser.add_argument("--media", type=Path, help="kesim için kaynak medya")
    parser.add_argument("--cut", type=int, metavar="N", help="N numaralı eşleşmeyi kes (1'den)")
    parser.add_argument("--splice", action="store_true", help="tüm eşleşmeleri birleştir")
    parser.add_argument(
        "--assemble",
        action="store_true",
        help="cümleyi tek tek kelimelerden kur (kelime cımbızlama testi)",
    )
    parser.add_argument("--pad-ms", type=int, default=0, help="kenarlara pay ekle")
    parser.add_argument("--out", type=Path, default=Path("cut.wav"))
    args = parser.parse_args()

    doc = json.loads(args.doc.read_text(encoding="utf-8"))

    if args.assemble:
        chosen, missing = assemble_words(doc, args.phrase)
        if missing:
            print(f"Kütüphanede olmayan kelimeler: {', '.join(missing)}")
        if not chosen:
            return 1

        print(f'"{args.phrase}" -> {len(chosen)} kelime toplandı\n')
        total = 0
        for index, word in enumerate(chosen, start=1):
            duration = word["end_ms"] - word["start_ms"]
            total += duration
            print(
                f"  {index}. {word['norm']:<10} {word['take_id']} "
                f"satır={word['line_id']} [{word['start_ms']}-{word['end_ms']} ms, "
                f"{duration} ms] güven={word.get('confidence', 0):.2f}"
            )
        print(f"\n  toplam süre: {total} ms")

        if not args.media:
            return 0
        if not args.media.exists():
            print(f"\nMedya bulunamadı: {args.media}", file=sys.stderr)
            return 1

        splice(
            args.media,
            [(w["start_ms"], w["end_ms"]) for w in chosen],
            args.out,
            args.pad_ms,
        )
        print(f"\nKelimeler birleştirildi -> {args.out}")
        print("ŞİMDİ DİNLE: kelimeler tam mı, başları/sonları kırpılmış mı?")
        return 0

    matches = find_phrase(doc, args.phrase)

    if not matches:
        print(f'"{args.phrase}" bulunamadı.')
        print("\nKütüphanedeki kelimeler:")
        seen = {
            schema.normalize_word(w["word"])
            for take in doc["takes"]
            for line in take["lines"]
            for w in line["words"]
        }
        print("  " + " ".join(sorted(seen)))
        return 1

    print(f'"{args.phrase}" -> {len(matches)} eşleşme\n')
    for index, match in enumerate(matches, start=1):
        duration = match["end_ms"] - match["start_ms"]
        tone = match["tone"] or "?"
        print(
            f"  {index}. {match['take_id']} kam={match['camera'] or '-'} "
            f"satır={match['line_id']} ton={tone} "
            f"[{match['start_ms']} - {match['end_ms']} ms, {duration} ms]"
        )
        print("     " + "  ".join(
            f"{w['word']}({w['start_ms']}-{w['end_ms']})" for w in match["words"]
        ))

    if not (args.cut or args.splice):
        return 0

    if not args.media:
        print("\n--cut / --splice için --media gerekiyor.", file=sys.stderr)
        return 1
    if not args.media.exists():
        print(f"\nMedya bulunamadı: {args.media}", file=sys.stderr)
        return 1

    if args.splice:
        spans = [(m["start_ms"], m["end_ms"]) for m in matches]
        splice(args.media, spans, args.out, args.pad_ms)
        print(f"\n{len(spans)} parça birleştirildi -> {args.out}")
        print("ŞİMDİ DİNLE: birleşim noktaları temiz mi, kelime kırpılmış mı?")
    else:
        if not 1 <= args.cut <= len(matches):
            print(f"\n--cut 1..{len(matches)} arasında olmalı.", file=sys.stderr)
            return 1
        match = matches[args.cut - 1]
        cut(args.media, match["start_ms"], match["end_ms"], args.out, args.pad_ms)
        print(f"\n{match['take_id']} eşleşme {args.cut} -> {args.out}")
        print("ŞİMDİ DİNLE: baştan ve sondan kelime kırpılmış mı?")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
