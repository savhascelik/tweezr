"""ClickHouse queries — single source of truth.

The SQL lives here and query strings are not built anywhere else. Parameters go through
ClickHouse's server-side binding ({name:Type}); there is no string interpolation.

WHY `projects` IS A LIST
A visitor's uploads go into a project of their own, derived from their session cookie, so
one person's footage does not appear in everyone else's library. A search therefore spans
two projects: the shared demo corpus and the caller's own uploads. The read queries take
an array so that stays a parameter rather than two queries stitched together in Python.

Why phrase search is here and why it looks like this:

Joining the table to itself once per word means N table scans. Instead this uses
ClickHouse's higher-order array functions — one pass, no joins:

  1. Find candidate lines by the phrase's FIRST word.
     With a primary key of (project_id, word_norm, start_ms) that comes straight off
     the index rather than scanning. It is the entire reason the schema's ORDER BY is
     shaped that way.
  2. Collect those lines' words into an array ordered by start_ms.
  3. Look for the consecutive match with arraySlice. If a line contains the phrase
     more than once, every occurrence comes back.

groupArray's order is NOT guaranteed, which is why we build an array of tuples and
arraySort it.
"""

from __future__ import annotations

# Single-word search: "where does this word occur in the library".
# A pure index read, the cheapest query here.
WORD_OCCURRENCES = """
SELECT
    take_id,
    line_id,
    camera,
    speaker,
    scene,
    tone,
    tone_score,
    source_url,
    word,
    start_ms,
    end_ms,
    confidence
FROM words
WHERE project_id IN {projects:Array(String)}
  AND word_norm = {word:String}
  AND ({tone:String} = '' OR tone = {tone:String})
ORDER BY confidence DESC, take_id, start_ms
LIMIT {limit:UInt32}
"""

# Phrase search. Returns per-line match arrays; the Python side flattens them.
PHRASE_MATCHES = """
WITH
    {phrase:Array(String)} AS phrase,
    length(phrase) AS n
SELECT
    take_id,
    line_id,
    -- Aliases must NOT shadow column names. Writing any(tone) AS tone makes the tone
    -- in WHERE resolve to the aggregate, and ClickHouse answers with
    -- ILLEGAL_AGGREGATION.
    any(camera)     AS line_camera,
    any(speaker)    AS line_speaker,
    any(scene)      AS line_scene,
    any(tone)       AS line_tone,
    any(tone_score) AS line_tone_score,
    any(source_url) AS line_source_url,
    arraySort(groupArray((start_ms, end_ms, word_norm, word))) AS ordered,
    arrayMap(t -> tupleElement(t, 3), ordered) AS norms,
    arrayFilter(i -> arraySlice(norms, i, n) = phrase, arrayEnumerate(norms)) AS hits,
    arrayMap(i -> tupleElement(ordered[i], 1), hits) AS hit_starts,
    arrayMap(i -> tupleElement(ordered[i + n - 1], 2), hits) AS hit_ends,
    arrayMap(
        i -> arrayStringConcat(
                 arrayMap(t -> tupleElement(t, 4), arraySlice(ordered, i, n)), ' '
             ),
        hits
    ) AS hit_texts
FROM words
WHERE project_id IN {projects:Array(String)}
  AND (take_id, line_id) IN (
      -- Index-friendly pre-filter: only lines containing the phrase's FIRST word.
      -- With a primary key of (project_id, word_norm, start_ms) this comes straight
      -- off the index. The tone filter is here too: tone is constant per line, so
      -- filtering here is equivalent to the outer WHERE but cuts earlier.
      SELECT take_id, line_id
      FROM words
      WHERE project_id IN {projects:Array(String)}
        AND word_norm = phrase[1]
        AND ({tone:String} = '' OR tone = {tone:String})
  )
GROUP BY take_id, line_id
HAVING length(hits) > 0
ORDER BY take_id, line_id
"""

# Token co-occurrence search: matches lines containing one or more search tokens.
TOKEN_MATCHES = """
WITH
    {phrase:Array(String)} AS phrase,
    length(phrase) AS n
SELECT
    take_id,
    line_id,
    any(camera)     AS line_camera,
    any(speaker)    AS line_speaker,
    any(scene)      AS line_scene,
    any(tone)       AS line_tone,
    any(tone_score) AS line_tone_score,
    any(source_url) AS line_source_url,
    arraySort(groupArray((start_ms, end_ms, word_norm, word))) AS ordered,
    arrayMap(t -> tupleElement(t, 3), ordered) AS norms,
    arrayIntersect(norms, phrase) AS matched_tokens,
    length(matched_tokens) AS token_count,
    arrayMin(arrayMap(t -> tupleElement(t, 1), arrayFilter(t -> has(phrase, tupleElement(t, 3)), ordered))) AS hit_start,
    arrayMax(arrayMap(t -> tupleElement(t, 2), arrayFilter(t -> has(phrase, tupleElement(t, 3)), ordered))) AS hit_end,
    arrayStringConcat(arrayMap(t -> tupleElement(t, 4), ordered), ' ') AS full_text
FROM words
WHERE project_id IN {projects:Array(String)}
  AND (take_id, line_id) IN (
      SELECT take_id, line_id
      FROM words
      WHERE project_id IN {projects:Array(String)}
        AND has(phrase, word_norm)
        AND ({tone:String} = '' OR tone = {tone:String})
  )
GROUP BY take_id, line_id
HAVING token_count > 0
ORDER BY token_count DESC, line_tone_score DESC, take_id, line_id
LIMIT {limit:UInt32}
"""

# Semantic vector similarity search over dialogue lines.
SEMANTIC_MATCHES = """
SELECT
    take_id,
    line_id,
    camera,
    speaker,
    scene,
    tone,
    tone_score,
    source_url,
    text,
    start_ms,
    end_ms,
    1.0 - cosineDistance(embedding, {query_vec:Array(Float32)}) AS semantic_score
FROM lines
WHERE project_id IN {projects:Array(String)}
  AND length(embedding) > 0
  AND ({tone:String} = '' OR tone = {tone:String})
ORDER BY semantic_score DESC
LIMIT {limit:UInt32}
"""

# Every word of specific lines, in order. What makes word-level tweezing possible.
#
# Phrase search returns the matched range only, so the interface could show the phrase
# but not the sentence it sits in — and a highlight covering 100% of the text explains
# nothing. This fetches the whole line so the words around the match are clickable and a
# range can be picked by hand.
#
# Batched over (take_id, line_id) pairs rather than one request per line: a search
# returns up to fifty candidates and fifty round trips to answer one question is the
# wrong shape. The pair tuple is bound server-side like every other parameter here.
LINE_WORDS = """
SELECT
    take_id,
    line_id,
    word,
    word_norm,
    start_ms,
    end_ms,
    confidence
FROM words
WHERE project_id IN {projects:Array(String)}
  AND (take_id, line_id) IN {pairs:Array(Tuple(String, UInt32))}
ORDER BY take_id, line_id, start_ms
"""

# One real line from the library, used as the search box's example.
#
# The example has to come from the data rather than a constant, because the constant was
# an English sentence and the library can be in any language. A hint in a language the
# footage is not in is worse than no hint.
#
# The longest line is chosen deliberately: a three-word line makes a poor example of
# phrase search, and length correlates with being a real sentence rather than a stray
# interjection.
SAMPLE_LINE = """
SELECT arrayStringConcat(
           arrayMap(t -> tupleElement(t, 2), arraySort(groupArray((start_ms, word)))),
           ' '
       ) AS text
FROM words
WHERE project_id IN {projects:Array(String)}
GROUP BY take_id, line_id
ORDER BY count() DESC, take_id, line_id
LIMIT 1
"""

# Every distinct word in the visible library, with how often it is spoken.
#
# WHY THIS EXISTS
# The product is search-first: you type a phrase and get candidates. That works for a
# corpus you already know, and falls apart on footage you just brought in — you have to
# guess a word, and what the transcriber heard is not always what you said. A search that
# comes back empty then reads as a broken product rather than a missed guess. This makes
# the vocabulary itself visible, so picking a word is a click instead of a memory test.
#
# It is a GROUP BY over word_norm, which is the primary key's second column, so it comes
# off the index in one pass.
#
# `max(word)` picks the display spelling. NOT `any()`: any() may return a different
# variant on every run, so a panel that refreshes would shuffle "The" and "the" for no
# reason. Byte order puts lowercase last among ASCII variants, which is the spelling you
# want in nearly every case, and when only one variant exists it is the only answer.
#
# Aliased to `spelling` rather than `word` because an alias must not shadow a column name
# — see the note in PHRASE_MATCHES.
#
# The tone filter is here so the panel can show the vocabulary of the CURRENT filter.
# Without it a chip could report five occurrences and then return nothing when clicked,
# because the filter excluded all five.
VOCABULARY = """
SELECT
    word_norm,
    max(word)          AS spelling,
    count()            AS occurrences,
    uniqExact(take_id) AS takes
FROM words
WHERE project_id IN {projects:Array(String)}
  AND ({take:String} = '' OR take_id = {take:String})
  AND ({tone:String} = '' OR tone = {tone:String})
GROUP BY word_norm
ORDER BY occurrences DESC, word_norm
LIMIT {limit:UInt32}
"""

# One row per recording. Behind the vocabulary panel's scope selector.
#
# An unknown take_id needs no validation anywhere: the project filter is what confines the
# read, so a take from someone else's session simply matches nothing.
#
# `duration_ms` rather than `end_ms`: the alias would shadow a column, and the last word's
# end is what the interface wants to show anyway.
TAKE_INVENTORY = """
SELECT
    take_id,
    uniqExact(line_id) AS lines,
    count()            AS words,
    max(end_ms)        AS duration_ms
FROM words
WHERE project_id IN {projects:Array(String)}
GROUP BY take_id
ORDER BY take_id
"""

# The agent's "what is in the library" question. Behind the get_library_stats tool.
LIBRARY_STATS = """
SELECT
    count()                        AS words,
    uniqExact(take_id)             AS takes,
    uniqExact(word_norm)           AS vocabulary,
    uniqExact(speaker)             AS speakers,
    round(max(end_ms) / 1000, 1)   AS longest_take_seconds,
    groupUniqArray(tone)           AS tones
FROM words
WHERE project_id IN {projects:Array(String)}
"""

# Singular on purpose: dropping is destructive, and an array parameter here would make
# "clear one project" and "clear several" look identical at the call site.
DROP_PROJECT = """
ALTER TABLE words DELETE WHERE project_id = {project:String}
"""

DROP_PROJECT_LINES = """
ALTER TABLE lines DELETE WHERE project_id = {project:String}
"""


def expand_phrase_rows(rows: list[dict]) -> list[dict]:
    """Unpacks PHRASE_MATCHES' per-line arrays into individual matches.

    The result has to have the same shape as verify_cut.find_phrase(), so that the two
    implementations can be asserted to agree.
    """
    matches: list[dict] = []
    for row in rows:
        for start_ms, end_ms, text in zip(
            row["hit_starts"], row["hit_ends"], row["hit_texts"]
        ):
            matches.append(
                {
                    "take_id": row["take_id"],
                    "line_id": row["line_id"],
                    "camera": row["line_camera"],
                    "speaker": row["line_speaker"],
                    "scene": row["line_scene"],
                    "tone": row["line_tone"],
                    "tone_score": float(row["line_tone_score"]),
                    "source_url": row["line_source_url"],
                    "start_ms": int(start_ms),
                    "end_ms": int(end_ms),
                    "text": text,
                    "match_type": "exact",
                }
            )
    matches.sort(key=lambda m: (m["take_id"], m["start_ms"]))
    return matches


def expand_token_rows(rows: list[dict], query_len: int) -> list[dict]:
    """Unpacks TOKEN_MATCHES rows into candidate match records."""
    matches: list[dict] = []
    for row in rows:
        token_count = int(row.get("token_count", 0))
        match_ratio = round(min(1.0, token_count / max(1, query_len)), 3)
        matches.append(
            {
                "take_id": row["take_id"],
                "line_id": row["line_id"],
                "camera": row["line_camera"],
                "speaker": row["line_speaker"],
                "scene": row["line_scene"],
                "tone": row["line_tone"],
                "tone_score": float(row["line_tone_score"]),
                "source_url": row["line_source_url"],
                "start_ms": int(row["hit_start"]),
                "end_ms": int(row["hit_end"]),
                "text": row["full_text"],
                "match_type": "tokens",
                "token_count": token_count,
                "score": match_ratio,
            }
        )
    return matches


def expand_semantic_rows(rows: list[dict]) -> list[dict]:
    """Unpacks SEMANTIC_MATCHES rows into candidate match records."""
    matches: list[dict] = []
    for row in rows:
        score = round(float(row.get("semantic_score", 0.0)), 3)
        matches.append(
            {
                "take_id": row["take_id"],
                "line_id": row["line_id"],
                "camera": row["camera"],
                "speaker": row["speaker"],
                "scene": row["scene"],
                "tone": row["tone"],
                "tone_score": float(row["tone_score"]),
                "source_url": row["source_url"],
                "start_ms": int(row["start_ms"]),
                "end_ms": int(row["end_ms"]),
                "text": row["text"],
                "match_type": "semantic",
                "score": score,
            }
        )
    return matches

