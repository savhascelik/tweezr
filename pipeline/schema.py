"""Veri kontratı — tek doğruluk kaynağı.

Hem elle yazılan fixture, hem Whisper çıktısı, hem ClickHouse satırları buradan geçiyor.
Normalizasyon tek yerde durduğu için fixture ile pipeline arasında sapma olamaz.

Ingest dokümanı (iç içe, insan okuyabilir):

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

flatten_rows() bunu ClickHouse `words` tablosunun satırlarına çeviriyor.
`word_norm` fixture'da YOK — türetiliyor. Kasıtlı: iki tarafta elle yazılırsa kayar.
"""

from __future__ import annotations

import unicodedata

# ClickHouse kolon sırası. insert() bu sırayı bekliyor.
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

# Kelime kenarlarından atılacak noktalama. Kelime İÇİNDEKİ kesme işareti korunuyor
# ("don't" tek kelime kalsın), tire de korunuyor ("well-known").
_EDGE_PUNCT = "\"'`.,!?;:()[]{}<>…—–-*_"


def normalize_word(word: str) -> str:
    """Arama anahtarı. Küçük harf, kenar noktalaması atılmış, Unicode NFKC.

    >>> normalize_word('"Asked,')
    'asked'
    >>> normalize_word("don't")
    "don't"
    """
    text = unicodedata.normalize("NFKC", word).strip()
    return text.strip(_EDGE_PUNCT).casefold()


def normalize_phrase(phrase: str) -> list[str]:
    """Aranan cümleyi normalize kelime listesine çevirir. Boşlar düşer."""
    return [w for w in (normalize_word(p) for p in phrase.split()) if w]


def flatten_rows(doc: dict) -> list[tuple]:
    """Ingest dokümanı -> ClickHouse satırları. Sıra COLUMNS ile aynı."""
    project_id = doc["project_id"]
    rows: list[tuple] = []

    for take in doc["takes"]:
        for line in take["lines"]:
            tone = line.get("tone") or "neutral"
            if tone not in TONES:
                raise ValueError(
                    f"{take['take_id']} satır {line['line_id']}: bilinmeyen ton {tone!r}. "
                    f"Geçerli: {TONES}"
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
    """Ingest dokümanını kontrol eder. Sorun listesi döner, boşsa temiz.

    Hizalama kalitesini de burada ölçüyoruz — asıl riskimiz o.
    """
    problems: list[str] = []

    if not doc.get("project_id"):
        problems.append("project_id boş")
    if not doc.get("takes"):
        problems.append("takes boş")

    for take in doc.get("takes", []):
        tid = take.get("take_id", "<isimsiz>")
        if not take.get("lines"):
            problems.append(f"{tid}: satır yok")

        seen_lines = set()
        for line in take.get("lines", []):
            lid = line.get("line_id")
            if lid in seen_lines:
                problems.append(f"{tid}: line_id {lid} tekrar ediyor")
            seen_lines.add(lid)

            words = line.get("words", [])
            if not words:
                problems.append(f"{tid}/{lid}: kelime yok")
                continue

            # Metin ile kelime dizisi tutuyor mu
            if line.get("text"):
                from_words = " ".join(
                    n for n in (normalize_word(w["word"]) for w in words) if n
                )
                from_text = " ".join(normalize_phrase(line["text"]))
                if from_words != from_text:
                    problems.append(
                        f"{tid}/{lid}: text ile words uyuşmuyor\n"
                        f"    text : {from_text}\n"
                        f"    words: {from_words}"
                    )

            prev_end = None
            for word in words:
                start, end = int(word["start_ms"]), int(word["end_ms"])
                label = f"{tid}/{lid} {word['word']!r}"

                if end <= start:
                    problems.append(f"{label}: süre sıfır veya negatif ({start}->{end})")
                elif end - start > 3000:
                    problems.append(f"{label}: {end - start} ms, tek kelime için fazla uzun")

                if prev_end is not None and start < prev_end:
                    problems.append(
                        f"{label}: önceki kelimeyle {prev_end - start} ms çakışıyor"
                    )
                prev_end = end

    return problems


def alignment_report(doc: dict) -> dict:
    """Hizalama kalitesi istatistikleri. 'Whisper temiz kesiyor mu' sorusunun sayısal tarafı."""
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
