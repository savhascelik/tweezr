-- The ClickHouse retrieval table. One row per spoken word.
-- Column order has to match schema.py COLUMNS exactly.

CREATE TABLE IF NOT EXISTS words
(
    project_id   LowCardinality(String),
    take_id      LowCardinality(String),
    source_url   String,                -- provenance, so a fragment can link to its source
    scene        LowCardinality(String),
    camera       LowCardinality(String),
    speaker      LowCardinality(String),
    line_id      UInt32,                -- tone is decided at this level
    word         String,                -- as spoken, which is what appears on screen
    word_norm    String,                -- the search key, from schema.normalize_word()
    start_ms     UInt32,
    end_ms       UInt32,
    confidence   Float32,
    tone         Enum8('neutral' = 0, 'calm' = 1, 'tense' = 2,
                       'angry' = 3, 'whisper' = 4, 'shouted' = 5),
    tone_score   Float32
)
ENGINE = MergeTree
ORDER BY (project_id, word_norm, start_ms);

-- word_norm sits second in the ORDER BY so that "where does this word occur in the
-- library" comes straight off the primary index instead of scanning.

-- Reference query: every occurrence of a word, filtered by delivery.
-- Phrase matching adds consecutive start_ms verification on top of this
-- (reference implementation: verify_cut.find_phrase).
--
-- SELECT take_id, camera, line_id, tone, start_ms, end_ms, confidence, source_url
-- FROM words
-- WHERE project_id = 'demo' AND word_norm = 'asked' AND tone = 'calm'
-- ORDER BY confidence DESC, start_ms
-- LIMIT 20;
