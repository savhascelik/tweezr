"""ClickHouse sorguları — tek doğruluk kaynağı.

SQL burada duruyor, başka yerde string kurulmuyor. Parametreler ClickHouse'un sunucu
tarafı binding'iyle geçiyor ({name:Type}), string interpolasyon yok.

Cümle araması neden burada ve neden böyle:

Kelime kelime self-join yapmak N tablo taraması demek. Yerine ClickHouse'un yüksek
mertebeden dizi fonksiyonlarını kullanıyoruz — tek geçiş, join yok:

  1. Cümlenin İLK kelimesiyle aday satırları bul.
     Primary key (project_id, word_norm, start_ms) olduğu için bu doğrudan index'ten
     geliyor, tam tarama yok. Şemanın ORDER BY seçimi tam bunun için.
  2. Aday satırların kelimelerini start_ms'e göre sıralı diziye topla.
  3. arraySlice ile ardışık eşleşmeyi ara. Bir satırda birden çok geçiş varsa
     hepsi dönüyor.

groupArray'in sırası garanti DEĞİL, o yüzden tuple dizisi kurup arraySort ediyoruz.
"""

from __future__ import annotations

# Kelime bazlı arama: "bu kelime kütüphanede nerede geçiyor".
# Saf index okuması, en ucuz sorgu.
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
WHERE project_id = {project:String}
  AND word_norm = {word:String}
  AND ({tone:String} = '' OR tone = {tone:String})
ORDER BY confidence DESC, take_id, start_ms
LIMIT {limit:UInt32}
"""

# Cümle araması. Satır başına eşleşme dizileri döner; Python tarafı düzleştiriyor.
PHRASE_MATCHES = """
WITH
    {phrase:Array(String)} AS phrase,
    length(phrase) AS n
SELECT
    take_id,
    line_id,
    -- Alias'lar kolon adlarını GÖLGELEMEMELİ. any(tone) AS tone yazınca WHERE'deki
    -- tone aggregate'e çözülüyor ve ClickHouse ILLEGAL_AGGREGATION veriyor.
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
WHERE project_id = {project:String}
  AND (take_id, line_id) IN (
      -- Index dostu ön filtre: sadece cümlenin İLK kelimesini içeren satırlar.
      -- Primary key (project_id, word_norm, start_ms) olduğu için bu doğrudan
      -- index'ten geliyor. Ton filtresi de burada: ton satır başına sabit,
      -- o yüzden burada süzmek dıştaki WHERE ile eşdeğer ama daha erken kesiyor.
      SELECT take_id, line_id
      FROM words
      WHERE project_id = {project:String}
        AND word_norm = phrase[1]
        AND ({tone:String} = '' OR tone = {tone:String})
  )
GROUP BY take_id, line_id
HAVING length(hits) > 0
ORDER BY take_id, line_id
"""

# Ajanın "kütüphanede ne var" sorusu. get_library_stats aracının arkası.
LIBRARY_STATS = """
SELECT
    count()                        AS words,
    uniqExact(take_id)             AS takes,
    uniqExact(word_norm)           AS vocabulary,
    uniqExact(speaker)             AS speakers,
    round(max(end_ms) / 1000, 1)   AS longest_take_seconds,
    groupUniqArray(tone)           AS tones
FROM words
WHERE project_id = {project:String}
"""

DROP_PROJECT = """
ALTER TABLE words DELETE WHERE project_id = {project:String}
"""


def expand_phrase_rows(rows: list[dict]) -> list[dict]:
    """PHRASE_MATCHES'in satır başına dizilerini tek tek eşleşmelere açar.

    Sonuç verify_cut.find_phrase() ile aynı şekilde olmak zorunda — iki uygulamanın
    aynı cevabı verdiğini doğrulayabilmek için.
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
                    # SQL tarafında line_ önekli, çünkü alias kolon adını gölgelememeli.
                    # Dışa açılan isimler sade kalıyor.
                    "camera": row["line_camera"],
                    "speaker": row["line_speaker"],
                    "scene": row["line_scene"],
                    "tone": row["line_tone"],
                    "tone_score": row["line_tone_score"],
                    "source_url": row["line_source_url"],
                    "start_ms": int(start_ms),
                    "end_ms": int(end_ms),
                    "text": text,
                }
            )
    matches.sort(key=lambda m: (m["take_id"], m["start_ms"]))
    return matches
