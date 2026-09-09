"""Line search through ClickHouse. What sits behind the agent's find_line tool.

    python -m pipeline.search --phrase "I never asked for this"
    python -m pipeline.search --phrase "I never asked for this" --tone calm
    python -m pipeline.search --word asked
    python -m pipeline.search --phrase "..." --compare scratch\\out.json

--compare is the important one: it asserts the SQL returns the SAME answer as the local
reference implementation (verify_cut.find_phrase). If those two diverge we start
returning the wrong takes silently, and this mode is what catches it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import db, queries, schema


def as_projects(project: str | list[str] | tuple[str, ...]) -> list[str]:
    """One project or several, normalised to the list the SQL expects.

    Accepting a plain string keeps every existing caller — the CLI, the agent tools —
    working unchanged, while the server can pass "the demo corpus plus this visitor's own
    uploads" without a second code path.
    """
    if isinstance(project, str):
        return [project]
    return [str(item) for item in project if item]


def phrase_search(
    client, project: str | list[str], phrase: str, tone: str = ""
) -> list[dict]:
    words = schema.normalize_phrase(phrase)
    if not words:
        return []
    result = client.query(
        queries.PHRASE_MATCHES,
        parameters={"projects": as_projects(project), "phrase": words, "tone": tone},
    )
    rows = [dict(zip(result.column_names, row)) for row in result.result_rows]
    return queries.expand_phrase_rows(rows)


def line_words(
    client, project: str | list[str], pairs: list[tuple[str, int]]
) -> dict[str, list[dict]]:
    """Every word of the given lines, keyed by `take_id:line_id`.

    Phrase search returns the matched range only. This returns the sentence around it, so
    the interface can make each word clickable and let a range be picked by hand — which
    is the whole point of tweezing at word level rather than at line level.

    One query for all the pairs. Fifty candidates would otherwise mean fifty round trips
    to answer a single question.
    """
    if not pairs:
        return {}

    result = client.query(
        queries.LINE_WORDS,
        parameters={
            "projects": as_projects(project),
            "pairs": [(str(t), int(l)) for t, l in pairs],
        },
    )

    lines: dict[str, list[dict]] = {}
    for row in result.result_rows:
        row = dict(zip(result.column_names, row))
        key = f"{row['take_id']}:{row['line_id']}"
        lines.setdefault(key, []).append(
            {
                "word": row["word"],
                "word_norm": row["word_norm"],
                "start_ms": int(row["start_ms"]),
                "end_ms": int(row["end_ms"]),
                "confidence": round(float(row["confidence"]), 3),
            }
        )
    return lines


def vocabulary(
    client,
    project: str | list[str],
    take: str = "",
    tone: str = "",
    limit: int = 240,
) -> list[dict]:
    """Every distinct word in the library, most spoken first.

    What the interface needs to stop being a memory test. Search-first is fine for a corpus
    you know; on footage you just uploaded you have to guess a word, and the transcriber
    does not always hear what you said. Showing the vocabulary turns that guess into a
    click.

    `spelling` is what gets displayed AND what a click searches for. It normalises back to
    the same `word_norm` returned here, so a chip can never lead to an empty result — the
    tests pin that both ways.
    """
    result = client.query(
        queries.VOCABULARY,
        parameters={
            "projects": as_projects(project),
            "take": take,
            "tone": tone,
            "limit": limit,
        },
    )
    rows = [dict(zip(result.column_names, r)) for r in result.result_rows]
    entries = []
    for row in rows:
        # Cleaned here rather than in SQL, because the normalisation rules live in schema
        # and splitting them across two languages is how they drift apart. A stored
        # spelling can end a sentence, and a chip reading "go." is not presentable.
        word = schema.display_word(row["spelling"])
        entries.append(
            {
                "key": row["word_norm"],
                # A word made entirely of punctuation would clean down to nothing; fall
                # back to the key so the chip still has something to show.
                "word": word or row["word_norm"],
                "count": int(row["occurrences"]),
                "takes": int(row["takes"]),
            }
        )
    return entries


def take_inventory(client, project: str | list[str]) -> list[dict]:
    """One entry per recording, so the vocabulary panel can be narrowed to one take."""
    result = client.query(
        queries.TAKE_INVENTORY, parameters={"projects": as_projects(project)}
    )
    return [
        {
            "take_id": row["take_id"],
            "lines": int(row["lines"]),
            "words": int(row["words"]),
            "duration_ms": int(row["duration_ms"]),
        }
        for row in (dict(zip(result.column_names, r)) for r in result.result_rows)
    ]


def word_search(
    client, project: str | list[str], word: str, tone: str = "", limit: int = 50
) -> list[dict]:
    normalized = schema.normalize_word(word)
    result = client.query(
        queries.WORD_OCCURRENCES,
        parameters={
            "projects": as_projects(project),
            "word": normalized,
            "tone": tone,
            "limit": limit,
        },
    )
    return [dict(zip(result.column_names, row)) for row in result.result_rows]


def compare_with_reference(doc_path: Path, phrase: str, sql_matches: list[dict]) -> bool:
    """Compares the SQL result against the local reference implementation."""
    from . import verify_cut

    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    reference = verify_cut.find_phrase(doc, phrase)

    as_key = lambda m: (m["take_id"], m["line_id"], m["start_ms"], m["end_ms"])
    sql_keys = sorted(as_key(m) for m in sql_matches)
    ref_keys = sorted(as_key(m) for m in reference)

    print(f"\n=== comparison ===")
    print(f"  SQL        : {len(sql_keys)} matches")
    print(f"  reference  : {len(ref_keys)} matches")

    if sql_keys == ref_keys:
        print("  RESULT     : identical")
        return True

    print("  RESULT     : THEY DISAGREE")
    for key in sorted(set(sql_keys) - set(ref_keys)):
        print(f"    only in SQL        : {key}")
    for key in sorted(set(ref_keys) - set(sql_keys)):
        print(f"    only in reference  : {key}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="ClickHouse line search.")
    parser.add_argument("--project", default="demo")
    parser.add_argument("--phrase")
    parser.add_argument("--word")
    parser.add_argument("--tone", default="", help="calm, tense, whisper ...")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument(
        "--compare",
        type=Path,
        metavar="DOC",
        help="compare the SQL result against this document",
    )
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    if not (args.phrase or args.word or args.stats):
        parser.error("one of --phrase, --word or --stats is required")

    print(f"Connecting: {db.describe()}")
    client = db.connect()

    if args.stats:
        result = client.query(
            queries.LIBRARY_STATS, parameters={"projects": [args.project]}
        )
        print(f"\nLibrary ({args.project}):")
        for name, value in zip(result.column_names, result.result_rows[0]):
            print(f"  {name:22} {value}")

    if args.word:
        rows = word_search(client, args.project, args.word, args.tone, args.limit)
        print(f'\n"{args.word}" -> {len(rows)} occurrences')
        for index, row in enumerate(rows, start=1):
            print(
                f"  {index:>3}. {row['take_id']} cam={row['camera'] or '-'} "
                f"line={row['line_id']} tone={row['tone']} "
                f"[{row['start_ms']}-{row['end_ms']} ms] "
                f"conf={row['confidence']:.2f} {row['word']!r}"
            )

    if args.phrase:
        matches = phrase_search(client, args.project, args.phrase, args.tone)
        applied = f" (tone={args.tone})" if args.tone else ""
        print(f'\n"{args.phrase}"{applied} -> {len(matches)} matches')
        for index, match in enumerate(matches, start=1):
            duration = match["end_ms"] - match["start_ms"]
            print(
                f"  {index}. {match['take_id']} cam={match['camera'] or '-'} "
                f"line={match['line_id']} tone={match['tone']} "
                f"({match['tone_score']:.2f}) "
                f"[{match['start_ms']}-{match['end_ms']} ms, {duration} ms]"
            )
            print(f"     {match['text']}")
            print(f"     source: {match['source_url']}")

        if args.compare:
            if not compare_with_reference(args.compare, args.phrase, matches):
                return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
