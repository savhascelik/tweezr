-- ClickHouse retrieval tablosu. Her kelime bir satır.
-- Kolon sırası schema.py COLUMNS ile AYNI olmak zorunda.

CREATE TABLE IF NOT EXISTS words
(
    project_id   LowCardinality(String),
    take_id      LowCardinality(String),
    source_url   String,                -- provenance: fragment kaynağına tıklanabilsin
    scene        LowCardinality(String),
    camera       LowCardinality(String),
    speaker      LowCardinality(String),
    line_id      UInt32,                -- ton bu seviyede belirleniyor
    word         String,                -- orijinal hali, ekranda gösterilen
    word_norm    String,                -- arama anahtarı, schema.normalize_word()
    start_ms     UInt32,
    end_ms       UInt32,
    confidence   Float32,
    tone         Enum8('neutral' = 0, 'calm' = 1, 'tense' = 2,
                       'angry' = 3, 'whisper' = 4, 'shouted' = 5),
    tone_score   Float32
)
ENGINE = MergeTree
ORDER BY (project_id, word_norm, start_ms);

-- ORDER BY'ın başında word_norm var: "bu kelime kütüphanede nerede geçiyor" sorgusu
-- doğrudan primary index'ten geliyor, tam tarama yok.

-- Referans sorgu: bir kelimenin tüm geçtiği yerler, tona göre filtreli.
-- Cümle eşleştirmesi bunun üstüne ardışık start_ms doğrulaması ekliyor
-- (referans uygulama: verify_cut.find_phrase).
--
-- SELECT take_id, camera, line_id, tone, start_ms, end_ms, confidence, source_url
-- FROM words
-- WHERE project_id = 'demo' AND word_norm = 'asked' AND tone = 'calm'
-- ORDER BY confidence DESC, start_ms
-- LIMIT 20;
