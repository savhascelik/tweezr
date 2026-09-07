"""ClickHouse üzerinden replik arama. ADK ajanının find_line aracının arkası.

    python -m pipeline.search --phrase "I never asked for this"
    python -m pipeline.search --phrase "I never asked for this" --tone calm
    python -m pipeline.search --word asked
    python -m pipeline.search --phrase "..." --compare scratch\out.json

--compare en önemlisi: SQL'in yerel referans uygulamasıyla (verify_cut.find_phrase)
AYNI cevabı verdiğini doğruluyor. İki uygulama ayrışırsa sessizce yanlış sonuç
döndürmeye başlarız; bu mod onu yakalıyor.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import db, queries, schema


def phrase_search(client, project: str, phrase: str, tone: str = "") -> list[dict]:
    words = schema.normalize_phrase(phrase)
    if not words:
        return []
    result = client.query(
        queries.PHRASE_MATCHES,
        parameters={"project": project, "phrase": words, "tone": tone},
    )
    rows = [dict(zip(result.column_names, row)) for row in result.result_rows]
    return queries.expand_phrase_rows(rows)


def word_search(
    client, project: str, word: str, tone: str = "", limit: int = 50
) -> list[dict]:
    normalized = schema.normalize_word(word)
    result = client.query(
        queries.WORD_OCCURRENCES,
        parameters={
            "project": project,
            "word": normalized,
            "tone": tone,
            "limit": limit,
        },
    )
    return [dict(zip(result.column_names, row)) for row in result.result_rows]


def compare_with_reference(doc_path: Path, phrase: str, sql_matches: list[dict]) -> bool:
    """SQL sonucunu yerel referans uygulamayla karşılaştırır."""
    from . import verify_cut

    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    reference = verify_cut.find_phrase(doc, phrase)

    as_key = lambda m: (m["take_id"], m["line_id"], m["start_ms"], m["end_ms"])
    sql_keys = sorted(as_key(m) for m in sql_matches)
    ref_keys = sorted(as_key(m) for m in reference)

    print(f"\n=== karşılaştırma ===")
    print(f"  SQL       : {len(sql_keys)} eşleşme")
    print(f"  referans  : {len(ref_keys)} eşleşme")

    if sql_keys == ref_keys:
        print("  SONUÇ     : birebir aynı")
        return True

    print("  SONUÇ     : AYRIŞMA VAR")
    for key in sorted(set(sql_keys) - set(ref_keys)):
        print(f"    sadece SQL'de      : {key}")
    for key in sorted(set(ref_keys) - set(sql_keys)):
        print(f"    sadece referansta  : {key}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="ClickHouse replik arama.")
    parser.add_argument("--project", default="demo")
    parser.add_argument("--phrase")
    parser.add_argument("--word")
    parser.add_argument("--tone", default="", help="calm, tense, whisper ...")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument(
        "--compare", type=Path, metavar="DOC", help="SQL sonucunu bu dokümanla karşılaştır"
    )
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    if not (args.phrase or args.word or args.stats):
        parser.error("--phrase, --word veya --stats gerekiyor")

    print(f"Bağlanıyor: {db.describe()}")
    client = db.connect()

    if args.stats:
        result = client.query(queries.LIBRARY_STATS, parameters={"project": args.project})
        print(f"\nKütüphane ({args.project}):")
        for name, value in zip(result.column_names, result.result_rows[0]):
            print(f"  {name:22} {value}")

    if args.word:
        rows = word_search(client, args.project, args.word, args.tone, args.limit)
        print(f'\n"{args.word}" -> {len(rows)} geçiş')
        for index, row in enumerate(rows, start=1):
            print(
                f"  {index:>3}. {row['take_id']} kam={row['camera'] or '-'} "
                f"satır={row['line_id']} ton={row['tone']} "
                f"[{row['start_ms']}-{row['end_ms']} ms] "
                f"güven={row['confidence']:.2f} {row['word']!r}"
            )

    if args.phrase:
        matches = phrase_search(client, args.project, args.phrase, args.tone)
        filtre = f" (ton={args.tone})" if args.tone else ""
        print(f'\n"{args.phrase}"{filtre} -> {len(matches)} eşleşme')
        for index, match in enumerate(matches, start=1):
            duration = match["end_ms"] - match["start_ms"]
            print(
                f"  {index}. {match['take_id']} kam={match['camera'] or '-'} "
                f"satır={match['line_id']} ton={match['tone']} "
                f"({match['tone_score']:.2f}) "
                f"[{match['start_ms']}-{match['end_ms']} ms, {duration} ms]"
            )
            print(f"     {match['text']}")
            print(f"     kaynak: {match['source_url']}")

        if args.compare:
            if not compare_with_reference(args.compare, args.phrase, matches):
                return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
