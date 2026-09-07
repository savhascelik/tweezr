"""Demo korpusunu ClickHouse'a yükler.

    python -m dev.load_demo

`demo/takes/*.json` içindeki hazır ingest dokümanlarını okuyup yazıyor. Whisper
çalıştırmıyor, Gemini çağırmıyor, GPU istemiyor — dolayısıyla dağıtılan ortamda da
koşabiliyor ve transkripsiyon yığınına ihtiyaç duymuyor.

Bölünme kasıtlı: `seed_demo` korpusu ÜRETİYOR (geliştirici makinesinde, ağır),
`load_demo` onu YÜKLÜYOR (her yerde, hafif). Dağıtılan örnek ikincisini çalıştırıyor.
"""

from __future__ import annotations

import json
import sys

from pipeline import db, ingest, search
from server import config


def main() -> int:
    documents = sorted(config.DEMO_TAKES_DIR.glob("*.json"))
    if not documents:
        print(f"{config.DEMO_TAKES_DIR} içinde doküman yok.", file=sys.stderr)
        print("Korpusu üretmek için: python -m dev.seed_demo", file=sys.stderr)
        return 1

    print(f"Bağlanıyor: {db.describe()}")
    try:
        client = db.connect()
    except Exception as error:
        print(f"ClickHouse'a bağlanamadı: {error}", file=sys.stderr)
        return 1

    db.create_table(client)

    total = 0
    missing_media: list[str] = []
    for index, path in enumerate(documents):
        doc = json.loads(path.read_text(encoding="utf-8"))
        # Yükleme sırasında medyanın varlığını kontrol ediyoruz: veritabanı dolu
        # ama dosya yok durumu, arama çalışıp oynatma çalışmaması demek olurdu.
        for take in doc["takes"]:
            name = take.get("source_url", "")
            if name and not (config.MEDIA_DIR / name).is_file():
                missing_media.append(f"{take['take_id']} -> {name}")

        written = ingest.ingest(client, doc, replace=(index == 0))
        total += written
        print(f"  {path.name:<20} {written:>5} satır")

    print(f"\n{total} satır yazıldı.")

    if missing_media:
        print(f"\nUYARI: {len(missing_media)} take'in medyası {config.MEDIA_DIR} altında yok:")
        for item in missing_media:
            print(f"  ! {item}")
        print("Arama çalışır ama oynatma ve render çalışmaz.")

    matches = search.phrase_search(client, config.DEMO_PROJECT, "I never asked for this")
    if matches:
        print(f'\nDoğrulama: "I never asked for this" -> {len(matches)} eşleşme')
        for match in matches:
            print(f"  {match['take_id']:9} {match['tone']:8} [{match['start_ms']}-{match['end_ms']} ms]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
