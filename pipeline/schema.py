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

# ClickHouse lines table column order for semantic embeddings and multi-token retrieval
LINE_COLUMNS = [
    "project_id",
    "take_id",
    "source_url",
    "scene",
    "camera",
    "speaker",
    "line_id",
    "text",
    "start_ms",
    "end_ms",
    "tone",
    "tone_score",
    "embedding",
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


def display_word(word: str) -> str:
    """The spelling to put on screen. Same cleanup as the search key, but keeps the case.

    The vocabulary panel shows words as chips and a click searches for the one clicked, so
    the text has to be presentable AND has to normalise back to the same key. The stored
    spelling is not, on its own: a word ending a sentence is stored as "go." and a chip
    reading "go." next to a search box that fills with "go." looks like a defect.

    Everything `normalize_word` does EXCEPT the two case operations, which is what keeps
    "I" from becoming "i" and "İstanbul" from becoming "istanbul" on screen.

    >>> display_word("go.")
    'go'
    >>> display_word('"Asked,')
    'Asked'
    >>> display_word("İstanbul")
    'İstanbul'
    >>> display_word("well-known")
    'well-known'

    The property the panel relies on: cleaning the spelling never changes which word it is.

    >>> normalize_word(display_word("go.")) == normalize_word("go.")
    True
    >>> normalize_word(display_word("Işık")) == normalize_word("Işık")
    True
    """
    text = unicodedata.normalize("NFKC", word).strip()
    # Stripped again at the end: removing a trailing comma from "hello ," exposes the space
    # that was behind it.
    return text.strip(_EDGE_PUNCT).strip()


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


def flatten_line_rows(
    doc: dict, embeddings: dict[tuple[str, int], list[float]] | None = None
) -> list[tuple]:
    """Ingest document -> ClickHouse lines rows, in LINE_COLUMNS order."""
    project_id = doc["project_id"]
    embeddings = embeddings or {}
    rows: list[tuple] = []

    for take in doc["takes"]:
        take_id = take["take_id"]
        for line in take["lines"]:
            tone = line.get("tone") or "neutral"
            if tone not in TONES:
                raise ValueError(
                    f"{take_id} line {line['line_id']}: unknown tone {tone!r}. "
                    f"Allowed: {TONES}"
                )
            words = line.get("words", [])
            start_ms = (
                int(words[0]["start_ms"]) if words else int(line.get("start_ms", 0))
            )
            end_ms = (
                int(words[-1]["end_ms"]) if words else int(line.get("end_ms", 0))
            )
            text = line.get("text") or " ".join(w["word"] for w in words)

            line_emb = (
                line.get("embedding")
                or embeddings.get((take_id, int(line["line_id"])))
                or []
            )

            rows.append(
                (
                    project_id,
                    take_id,
                    take.get("source_url", ""),
                    take.get("scene", ""),
                    take.get("camera", ""),
                    take.get("speaker", ""),
                    int(line["line_id"]),
                    text,
                    start_ms,
                    end_ms,
                    tone,
                    float(line.get("tone_score", 0.0)),
                    [float(x) for x in line_emb],
                )
            )
    return rows


def spoken_content(text: str) -> str:
    """Letters and digits only, for comparing what was said rather than how it was split.

    Word boundaries are not something this contract cares about, and two renderings of the
    same speech disagree about them constantly. Whisper's segment text keeps a hyphenated
    word whole while its word timestamps split it at the hyphen, so `lafrey-he` in the text
    is `lafrey-` and `he` in the words. Same speech, two tokenisations.

    >>> spoken_content("lafrey-he video") == spoken_content("lafrey- he video")
    True
    >>> spoken_content("Don't!") == spoken_content("don t")
    True
    >>> spoken_content("hello there") == spoken_content("hello world")
    False
    """
    return "".join(ch for ch in unicodedata.normalize("NFKC", text).casefold() if ch.isalnum())


def validate(doc: dict) -> list[str]:
    """FATAL problems only. Empty means the document can be ingested.

    "Fatal" means the rows would be wrong or unusable: a missing key, a duplicate line id,
    a word whose range is empty. Everything softer is in `warnings()`.

    That split exists because it was not there and it cost a user a paid ingest. The text
    and word list of a real take disagreed about one hyphen, and this function refused the
    whole thing — even though the line text never reaches ClickHouse at all. It is used for
    display and for the tone prompt; `words` is what gets indexed and cut. Refusing rows
    that were correct, over a field that is not stored, is the wrong severity.
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
            # A duplicate would make (take_id, line_id) ambiguous, and that pair is how
            # every line-level lookup addresses a line.
            if lid in seen_lines:
                problems.append(f"{tid}: line_id {lid} appears twice")
            seen_lines.add(lid)

            words = line.get("words", [])
            if not words:
                problems.append(f"{tid}/{lid}: no words")
                continue

            for word in words:
                start, end = int(word["start_ms"]), int(word["end_ms"])
                # An empty or inverted range would cut nothing, or ask ffmpeg for a
                # negative duration.
                if end <= start:
                    problems.append(
                        f"{tid}/{lid} {word['word']!r}: zero or negative duration "
                        f"({start}->{end})"
                    )

    return problems


def warnings(doc: dict) -> list[str]:
    """Things worth knowing that do NOT make a document unusable.

    All three of these fire on ordinary footage, which is exactly why they are not fatal:

    - Text and words disagreeing. Compared on content now rather than on tokenisation, so
      it only fires when the segment text and the word list describe different speech —
      which does happen when Whisper's segmenter and its word timestamps diverge, and is
      worth seeing without being worth refusing.
    - A word longer than three seconds. A heuristic. A drawn-out word, or one Whisper
      stretched across a pause, is real.
    - Words overlapping. faster-whisper emits small overlaps, and every word range is cut
      independently, so an overlap changes nothing about the output.
    """
    notes: list[str] = []

    for take in doc.get("takes", []):
        tid = take.get("take_id", "<unnamed>")
        for line in take.get("lines", []):
            lid = line.get("line_id")
            words = line.get("words", [])
            if not words:
                continue

            if line.get("text"):
                from_words = " ".join(
                    n for n in (normalize_word(w["word"]) for w in words) if n
                )
                from_text = " ".join(normalize_phrase(line["text"]))
                if spoken_content(from_words) != spoken_content(from_text):
                    notes.append(
                        f"{tid}/{lid}: text and words describe different speech\n"
                        f"    text : {from_text}\n"
                        f"    words: {from_words}"
                    )

            prev_end = None
            for word in words:
                start, end = int(word["start_ms"]), int(word["end_ms"])
                label = f"{tid}/{lid} {word['word']!r}"

                if end - start > 3000:
                    notes.append(f"{label}: {end - start} ms, long for one word")
                if prev_end is not None and start < prev_end:
                    notes.append(
                        f"{label}: overlaps the previous word by {prev_end - start} ms"
                    )
                prev_end = end

    return notes


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
