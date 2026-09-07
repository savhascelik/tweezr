"""ClickHouse sorgularının doğruluk testleri.

Cümle araması ürünün kalbi. Sessizce yanlış sonuç döndürmesi en pahalı hata olur,
o yüzden her senaryo hem SQL'de hem yerel referans uygulamada koşuluyor ve
sonuçlar karşılaştırılıyor.

    python test_queries.py

Yerel ClickHouse gerekiyor:
    docker compose -f ../dev/docker-compose.yml up -d
"""

from __future__ import annotations

import sys

import db
import queries
import schema
import search
import verify_cut

TEST_PROJECT = "__test__"


def word(text: str, start_ms: int, duration: int = 200) -> dict:
    return {
        "word": text,
        "start_ms": start_ms,
        "end_ms": start_ms + duration,
        "confidence": 0.95,
    }


def build_doc() -> dict:
    """Kenar durumları kasten içeren test korpusu."""
    return {
        "project_id": TEST_PROJECT,
        "takes": [
            {
                "take_id": "T01",
                "scene": "S01",
                "camera": "A",
                "speaker": "MAYA",
                "source_url": "gs://t/T01.mp4",
                "lines": [
                    # Aynı satırda cümle İKİ kere: arrayFilter çoklu eşleşme yolu
                    {
                        "line_id": 1,
                        "text": "go now go now",
                        "tone": "tense",
                        "tone_score": 0.9,
                        "words": [
                            word("go", 0), word("now", 250),
                            word("go", 500), word("now", 750),
                        ],
                    },
                    # Noktalama ve büyük harf: normalizasyon
                    {
                        "line_id": 2,
                        "text": "Go, now!",
                        "tone": "calm",
                        "tone_score": 0.7,
                        "words": [word("Go,", 2000), word("now!", 2250)],
                    },
                ],
            },
            {
                "take_id": "T02",
                "scene": "S01",
                "camera": "B",
                "speaker": "MAYA",
                "source_url": "gs://t/T02.mp4",
                "lines": [
                    # Kelimeler var ama SIRA yanlış: eşleşme OLMAMALI
                    {
                        "line_id": 1,
                        "text": "now go",
                        "tone": "whisper",
                        "tone_score": 0.6,
                        "words": [word("now", 100), word("go", 350)],
                    },
                ],
            },
        ],
    }


def check(name: str, actual, expected) -> bool:
    ok = actual == expected
    print(f"  {'GEÇTİ' if ok else 'BAŞARISIZ':<10} {name}")
    if not ok:
        print(f"             beklenen: {expected}")
        print(f"             gelen   : {actual}")
    return ok


def keys(matches: list[dict]) -> list[tuple]:
    return sorted(
        (m["take_id"], m["line_id"], m["start_ms"], m["end_ms"]) for m in matches
    )


def main() -> int:
    doc = build_doc()

    problems = schema.validate(doc)
    if problems:
        print("Test korpusu kontrata uymuyor:")
        for problem in problems:
            print(f"  ! {problem}")
        return 1

    print(f"Bağlanıyor: {db.describe()}")
    try:
        client = db.connect()
    except Exception as error:
        print(f"ClickHouse'a bağlanamadı: {error}", file=sys.stderr)
        print("  docker compose -f ../dev/docker-compose.yml up -d", file=sys.stderr)
        return 1

    db.create_table(client)
    client.command(queries.DROP_PROJECT, parameters={"project": TEST_PROJECT})
    client.insert("words", schema.flatten_rows(doc), column_names=schema.COLUMNS)

    results: list[bool] = []
    print("\n=== cümle araması ===")

    # Aynı satırda iki geçiş + ikinci satırda bir geçiş = 3
    sql = search.phrase_search(client, TEST_PROJECT, "go now")
    results.append(check("'go now' -> 3 eşleşme", len(sql), 3))
    results.append(
        check(
            "aynı satırda iki geçiş bulundu",
            keys([m for m in sql if m["line_id"] == 1]),
            [("T01", 1, 0, 450), ("T01", 1, 500, 950)],
        )
    )

    # Ters sıra eşleşmemeli
    results.append(
        check("T02 (ters sıra) eşleşmedi", [m for m in sql if m["take_id"] == "T02"], [])
    )

    # Noktalama ve büyük harf normalizasyonu
    results.append(
        check(
            "noktalamalı satır eşleşti",
            keys([m for m in sql if m["line_id"] == 2]),
            [("T01", 2, 2000, 2450)],
        )
    )

    # SQL ile yerel referans aynı cevabı vermeli
    reference = verify_cut.find_phrase(doc, "go now")
    results.append(check("SQL == yerel referans", keys(sql), keys(reference)))

    print("\n=== ton filtresi ===")
    calm = search.phrase_search(client, TEST_PROJECT, "go now", tone="calm")
    results.append(check("ton=calm -> 1 eşleşme", keys(calm), [("T01", 2, 2000, 2450)]))
    tense = search.phrase_search(client, TEST_PROJECT, "go now", tone="tense")
    results.append(check("ton=tense -> 2 eşleşme", len(tense), 2))
    results.append(
        check("ton=angry -> 0 eşleşme", search.phrase_search(
            client, TEST_PROJECT, "go now", tone="angry"), [])
    )

    print("\n=== bulunamayan ===")
    results.append(
        check("olmayan kelime", search.phrase_search(client, TEST_PROJECT, "helicopter"), [])
    )
    results.append(
        check(
            "kütüphaneden uzun cümle",
            search.phrase_search(client, TEST_PROJECT, "go now go now go now"),
            [],
        )
    )

    print("\n=== kelime araması ===")
    occurrences = search.word_search(client, TEST_PROJECT, "GO,")
    results.append(check("'GO,' normalize edilip 4 geçiş buldu", len(occurrences), 4))
    results.append(
        check(
            "kelime + ton filtresi",
            len(search.word_search(client, TEST_PROJECT, "go", tone="whisper")),
            1,
        )
    )

    client.command(queries.DROP_PROJECT, parameters={"project": TEST_PROJECT})

    passed = sum(results)
    print(f"\n{passed}/{len(results)} test geçti")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
