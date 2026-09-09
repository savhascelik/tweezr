"""The data contract — single source of truth.

The hand-written fixture, the Whisper output and the ClickHouse rows all pass through
here. Normalisation lives in exactly one place, so the fixture and the pipeline cannot
drift apart on what a word is.

Ingest document, nested and human-readable:

    {
      "project_id": "demo",
      "takes": [
        {
          "take_id": "S01_T03", "scene": "S01", "camera": "A",
          "speaker": "MAYA", "source_url": "...",
          "lines": [
            {
              "line_id": 1, "text": "...", "tone": "calm", "tone_score": 0.82,
              "words": [{"word": "I", "start_ms": 1200, "end_ms": 1310, "confidence": 0.99}]
            }
          ]
        }
      ]
    }

flatten_rows() turns that into rows of the ClickHouse `words` table.
`word_norm` is NOT in the fixture, it is derived. Deliberately: written by hand on both
sides it would eventually disagree with itself.
"""

from __future__ import annotations

import unicodedata

# ClickHouse column order. insert() expects exactly this.
COLUMNS = [
    "project_id",
    "take_id",
    "source_url",
    "scene",
    "camera",
    "speaker",
    "line_id",
    "word",
    "word_norm",
    "start_ms",
    "end_ms",
    "confidence",
    "tone",
    "tone_score",
]

TONES = ("neutral", "calm", "tense", "angry", "whisper", "shouted")

# Punctuation stripped from the edges of a word. An apostrophe INSIDE a word survives
# so "don't" stays one word, and so does a hyphen, so "well-known" stays one word.
_EDGE_PUNCT = "\"'`.,!?;:()[]{}<>…—–-*_"

# The dotted and dotless i, folded together for the search key only.
#
# The principle: the search key folds distinctions that capitalisation destroys. Case is
# the obvious one and casefold already handles it. The Turkish i is the other one, and it
# needs saying out loud:
#
#   "İ".casefold() is "i" + U+0307, a combining dot, which does NOT equal "i" — so
#   searching "istanbul" would not find "İstanbul".
#   Turkish capitalises "ı" as "I", and "I".casefold() is "i" — so a sentence-initial
#   "Işık" becomes "işık" while the word the editor types, "ışık", stays dotless.
#
# Both directions break search on real Turkish transcripts, and neither can be resolved
# without knowing the language of every individual word. Folding them costs the
# ı/i distinction in the KEY; the `word` column keeps the original spelling and that is
# what appears on screen.
_I_FOLD = str.maketrans({"\u0130": "i", "\u0131": "i", "I": "i"})


def normalize_word(word: str) -> str:
    """The search key. Case folded, edge punctuation removed, Unicode NFKC.

    Language independent by construction: casefold handles German, French, Greek and
    Cyrillic correctly, and scripts without case pass through untouched.

    >>> normalize_word('"Asked,')
    'asked'
    >>> normalize_word("don't")
    "don't"
    >>> normalize_word("Straße") == normalize_word("STRASSE")
    True
    >>> normalize_word("ÉCOLE")
    'école'

    The Turkish i, dotted and dotless, folds to plain i so that capitalisation cannot
    hide a word from search:

    >>> normalize_word("İstanbul") == normalize_word("istanbul")
    True
    >>> normalize_word("Işık") == normalize_word("ışık")
    True
    >>> normalize_word("DÜŞÜNCE")
    'düşünce'
    """
    text = unicodedata.normalize("NFKC", word).strip()
    return text.strip(_EDGE_PUNCT).translate(_I_FOLD).casefold()


def normalize_phrase(phrase: str) -> list[str]:
    """Turns a searched phrase into normalised words. Empty results drop out."""
    return [w for w in (normalize_word(p) for p in phrase.split()) if w]


def flatten_rows(doc: dict) -> list[tuple]:
    """Ingest document -> ClickHouse rows, in COLUMNS order."""
    project_id = doc["project_id"]
    rows: list[tuple] = []

    for take in doc["takes"]:
        for line in take["lines"]:
            tone = line.get("tone") or "neutral"
            if tone not in TONES:
                raise ValueError(
                    f"{take['take_id']} line {line['line_id']}: unknown tone {tone!r}. "
                    f"Allowed: {TONES}"
                )
            for word in line["words"]:
                rows.append(
                    (
                        project_id,
                        take["take_id"],
                        take.get("source_url", ""),
                        take.get("scene", ""),
                        take.get("camera", ""),
                        take.get("speaker", ""),
                        int(line["line_id"]),
                        word["word"],
                        normalize_word(word["word"]),
                        int(word["start_ms"]),
                        int(word["end_ms"]),
                        float(word.get("confidence", 0.0)),
                        tone,
                        float(line.get("tone_score", 0.0)),
                    )
                )
    return rows


def validate(doc: dict) -> list[str]:
    """Checks an ingest document. Returns a list of problems; empty means clean.

    Alignment quality is measured here too, since that is the real risk.
    """
    problems: list[str] = []

    if not doc.get("project_id"):
        problems.append("project_id is empty")
    if not doc.get("takes"):
        problems.append("takes is empty")

    for take in doc.get("takes", []):
        tid = take.get("take_id", "<unnamed>")
        if not take.get("lines"):
            problems.append(f"{tid}: no lines")

        seen_lines = set()
        for line in take.get("lines", []):
            lid = line.get("line_id")
            if lid in seen_lines:
                problems.append(f"{tid}: line_id {lid} appears twice")
            seen_lines.add(lid)

            words = line.get("words", [])
            if not words:
                problems.append(f"{tid}/{lid}: no words")
                continue

            # Does the text agree with the word sequence
            if line.get("text"):
                from_words = " ".join(
                    n for n in (normalize_word(w["word"]) for w in words) if n
                )
                from_text = " ".join(normalize_phrase(line["text"]))
                if from_words != from_text:
                    problems.append(
                        f"{tid}/{lid}: text and words disagree\n"
                        f"    text : {from_text}\n"
                        f"    words: {from_words}"
                    )

            prev_end = None
            for word in words:
                start, end = int(word["start_ms"]), int(word["end_ms"])
                label = f"{tid}/{lid} {word['word']!r}"

                if end <= start:
                    problems.append(f"{label}: zero or negative duration ({start}->{end})")
                elif end - start > 3000:
                    problems.append(f"{label}: {end - start} ms, too long for one word")

                if prev_end is not None and start < prev_end:
                    problems.append(
                        f"{label}: overlaps the previous word by {prev_end - start} ms"
                    )
                prev_end = end

    return problems


def alignment_report(doc: dict) -> dict:
    """Alignment quality statistics — the numeric half of "does Whisper cut cleanly"."""
    durations: list[int] = []
    gaps: list[int] = []
    zero_length = 0
    overlaps = 0
    words_total = 0

    for take in doc.get("takes", []):
        for line in take.get("lines", []):
            prev_end = None
            for word in line.get("words", []):
                start, end = int(word["start_ms"]), int(word["end_ms"])
                words_total += 1
                duration = end - start
                durations.append(duration)
                if duration <= 0:
                    zero_length += 1
                if prev_end is not None:
                    gap = start - prev_end
                    gaps.append(gap)
                    if gap < 0:
                        overlaps += 1
                prev_end = end

    def pct(values: list[int], q: float) -> int:
        if not values:
            return 0
        ordered = sorted(values)
        idx = min(len(ordered) - 1, int(q * (len(ordered) - 1)))
        return ordered[idx]

    return {
        "words": words_total,
        "zero_length_words": zero_length,
        "overlapping_words": overlaps,
        "duration_ms_p50": pct(durations, 0.50),
        "duration_ms_p95": pct(durations, 0.95),
        "duration_ms_max": max(durations, default=0),
        "gap_ms_p50": pct(gaps, 0.50),
        "gap_ms_max": max(gaps, default=0),
        "gap_ms_min": min(gaps, default=0),
    }
