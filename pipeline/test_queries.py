"""Correctness tests for the ClickHouse queries.

Phrase search is the heart of the product, and returning the wrong answer quietly is the
most expensive failure it can have. So every scenario runs through both the SQL and the
local reference implementation, and the results are compared.

    python -m pipeline.test_queries

Needs a local ClickHouse:
    docker compose -f dev/docker-compose.yml up -d
"""

from __future__ import annotations

import sys

from . import db, queries, schema, search, verify_cut

TEST_PROJECT = "__test__"


def word(text: str, start_ms: int, duration: int = 200) -> dict:
    return {
        "word": text,
        "start_ms": start_ms,
        "end_ms": start_ms + duration,
        "confidence": 0.95,
    }


def build_doc() -> dict:
    """A test corpus built to contain the edge cases on purpose."""
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
                    # The phrase TWICE in one line: the arrayFilter multi-match path
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
                    # Punctuation and capitals: normalisation
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
                    # The words are present but in the wrong ORDER: must NOT match
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
    print(f"  {'PASS' if ok else 'FAIL':<10} {name}")
    if not ok:
        print(f"             expected: {expected}")
        print(f"             actual  : {actual}")
    return ok


def keys(matches: list[dict]) -> list[tuple]:
    return sorted(
        (m["take_id"], m["line_id"], m["start_ms"], m["end_ms"]) for m in matches
    )


def main() -> int:
    doc = build_doc()

    problems = schema.validate(doc)
    if problems:
        print("The test corpus does not match the contract:")
        for problem in problems:
            print(f"  ! {problem}")
        return 1

    print(f"Connecting: {db.describe()}")
    try:
        client = db.connect()
    except Exception as error:
        print(f"Could not connect to ClickHouse: {error}", file=sys.stderr)
        print("  docker compose -f dev/docker-compose.yml up -d", file=sys.stderr)
        return 1

    db.create_table(client)
    client.command(queries.DROP_PROJECT, parameters={"project": TEST_PROJECT})
    client.insert("words", schema.flatten_rows(doc), column_names=schema.COLUMNS)

    results: list[bool] = []
    print("\n=== phrase search ===")

    # Two occurrences in one line plus one in the second line = 3
    sql = search.phrase_search(client, TEST_PROJECT, "go now")
    results.append(check("'go now' -> 3 matches", len(sql), 3))
    results.append(
        check(
            "both occurrences in one line found",
            keys([m for m in sql if m["line_id"] == 1]),
            [("T01", 1, 0, 450), ("T01", 1, 500, 950)],
        )
    )

    # Reversed order must not match
    results.append(
        check("T02 (reversed order) did not match", [m for m in sql if m["take_id"] == "T02"], [])
    )

    # Punctuation and capital normalisation
    results.append(
        check(
            "line with punctuation matched",
            keys([m for m in sql if m["line_id"] == 2]),
            [("T01", 2, 2000, 2450)],
        )
    )

    # The SQL and the local reference have to agree
    reference = verify_cut.find_phrase(doc, "go now")
    results.append(check("SQL == local reference", keys(sql), keys(reference)))

    print("\n=== tone filter ===")
    calm = search.phrase_search(client, TEST_PROJECT, "go now", tone="calm")
    results.append(check("tone=calm -> 1 match", keys(calm), [("T01", 2, 2000, 2450)]))
    tense = search.phrase_search(client, TEST_PROJECT, "go now", tone="tense")
    results.append(check("tone=tense -> 2 matches", len(tense), 2))
    results.append(
        check("tone=angry -> 0 matches", search.phrase_search(
            client, TEST_PROJECT, "go now", tone="angry"), [])
    )

    print("\n=== line words (word-level tweezing) ===")
    # Phrase search returns the matched range; this returns the sentence around it, which
    # is what lets a range be picked by hand instead of taking the whole match.
    lines = search.line_words(client, TEST_PROJECT, [("T01", 1), ("T01", 2), ("T02", 1)])
    results.append(check("three lines came back", sorted(lines), ["T01:1", "T01:2", "T02:1"]))
    results.append(
        check(
            "words in spoken order",
            [w["word"] for w in lines["T01:1"]],
            ["go", "now", "go", "now"],
        )
    )
    results.append(
        check(
            "each word carries its own range",
            [(w["start_ms"], w["end_ms"]) for w in lines["T01:1"]],
            [(0, 200), (250, 450), (500, 700), (750, 950)],
        )
    )
    # Normalisation is derived on ingest, not stored by the caller
    results.append(
        check("normalised form travels", [w["word_norm"] for w in lines["T01:2"]], ["go", "now"])
    )
    results.append(
        check("the original spelling is kept", [w["word"] for w in lines["T01:2"]], ["Go,", "now!"])
    )
    # A line that does not exist is simply absent, so the caller can ask for a batch
    # without first proving every pair is real
    results.append(
        check(
            "unknown pairs are absent, not an error",
            sorted(search.line_words(client, TEST_PROJECT, [("T01", 1), ("NOPE", 9)])),
            ["T01:1"],
        )
    )
    results.append(check("no pairs, no query", search.line_words(client, TEST_PROJECT, []), {}))
    # The pair is matched as a pair: T02 line 1 exists, T01 line 9 does not, and asking
    # for both must not cross-product into T02:9 or T01:1
    results.append(
        check(
            "pairs are matched as pairs, not as a cross product",
            sorted(search.line_words(client, TEST_PROJECT, [("T02", 1), ("T01", 9)])),
            ["T02:1"],
        )
    )

    print("\n=== no match ===")
    results.append(
        check("word not present", search.phrase_search(client, TEST_PROJECT, "helicopter"), [])
    )
    results.append(
        check(
            "phrase longer than the library",
            search.phrase_search(client, TEST_PROJECT, "go now go now go now"),
            [],
        )
    )

    print("\n=== word search ===")
    occurrences = search.word_search(client, TEST_PROJECT, "GO,")
    results.append(check("'GO,' normalised and found 4 occurrences", len(occurrences), 4))
    results.append(
        check(
            "word plus tone filter",
            len(search.word_search(client, TEST_PROJECT, "go", tone="whisper")),
            1,
        )
    )

    client.command(queries.DROP_PROJECT, parameters={"project": TEST_PROJECT})

    passed = sum(results)
    print(f"\n{passed}/{len(results)} tests passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
