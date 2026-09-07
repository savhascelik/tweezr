# pipeline — retrieval omurgası

Kütüphaneyi aranabilir hale getiren katman. UI yok, ajan yok.

```
medya ──▶ transcribe.py ──▶ tone.py ──▶ ingest.py ──▶ ClickHouse
          (kelime+ms)       (Gemini)                       │
                                                    search.py ◀── ajan buradan sorar
```

## İş bölümü

| İş | Araç | Neden |
| --- | --- | --- |
| Kelime + zaman kodu | `faster-whisper`, CPU | Hizalama problemi. Metni ve sesi biliyorsun, eşleşmeyi arıyorsun. |
| Ton | Gemini multimodal | Prozodi + anlam problemi. Whisper'ın yapamadığı tek iş. |
| Arama | ClickHouse | Kelime bazlı retrieval. Metrik kovası değil, motorun kendisi. |

## Dosyalar

| Dosya | İş |
| --- | --- |
| `schema.py` | Veri kontratı. Normalizasyon, düzleştirme, doğrulama, hizalama raporu. Tek doğruluk kaynağı. |
| `fixture.json` | Elle yazılmış kontrat örneği. Pipeline bu şekli üretmek zorunda. |
| `transcribe.py` | Medya → kelime + ms zaman kodu. |
| `tone.py` | Take başına 1 Gemini çağrısı → satır başına ton. |
| `queries.py` | SQL'in tamamı. Başka yerde string kurulmuyor. |
| `db.py` | ClickHouse bağlantısı, ortam değişkenlerinden. |
| `ingest.py` | Doküman → `words` tablosu. |
| `search.py` | Replik arama. `find_line` aracının arkası. |
| `test_queries.py` | Sorgu doğruluk testleri. |
| `verify_cut.py` | Kesim doğrulaması + yerel referans uygulama. |
| `schema.sql` | `words` tablosu. Kolon sırası `schema.py` ile aynı. |
| `make_test_audio.ps1` | Windows SAPI ile bilinen metinli test sesi. |

## Kurulum

```powershell
python -m venv ..\.venv
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
docker compose -f ..\dev\docker-compose.yml up -d
```

Sistem `ffmpeg`'ine gerek yok.

## Uçtan uca

```powershell
$py = "..\.venv\Scripts\python.exe"

# 0. Test sesi (kendi çekimin yoksa)
powershell -ExecutionPolicy Bypass -File make_test_audio.ps1
Move-Item sample.wav ..\scratch\

# 1. Kelime bazlı zaman kodu
& $py transcribe.py ..\scratch\sample.wav --take-id T01 --scene S01 --camera A `
    --speaker MAYA --out ..\scratch\T01.json

# 2. Ton  (anahtar yoksa --dry-run, hepsi neutral olur)
& $py tone.py ..\scratch\T01.json --media ..\scratch\sample.wav

# 3. ClickHouse'a yaz
& $py ingest.py ..\scratch\T01.json --replace

# 4. Ara
& $py search.py --phrase "I never asked for this"
& $py search.py --phrase "I never asked for this" --tone calm
& $py search.py --word asked
& $py search.py --stats
```

## Doğrulama

```powershell
& $py test_queries.py                       # SQL doğruluk testleri
python -m doctest schema.py                 # normalizasyon
& $py search.py --phrase "..." --compare ..\scratch\T01.json
```

`--compare` en önemlisi: SQL ile yerel referans uygulamanın (`verify_cut.find_phrase`)
aynı cevabı verdiğini doğruluyor. İkisi ayrışırsa sessizce yanlış sonuç dönmeye başlar.

## Kesimi kulakla doğrula

Sayısal rapor hizalamanın makul olduğunu söyler, kalitesine kulak karar verir.

```powershell
# Cümlenin geçtiği yerleri kesip birleştir
& $py verify_cut.py ..\scratch\T01.json --phrase "I never asked for this" `
    --media ..\scratch\sample.wav --splice --out ..\scratch\spliced.wav

# EN ZOR TEST: kelimeleri farklı yerlerden toplayıp yeni cümle kur
& $py verify_cut.py ..\scratch\T01.json --phrase "I asked for quiet on set" `
    --media ..\scratch\sample.wav --assemble --out ..\scratch\assembled.wav
```

Kelime kırpılmış geliyorsa ilk çevireceğin düğme `--pad-ms 40`.

## Ölçülen değerler

Windows, CPU, `base.en`, int8:

| | |
| --- | --- |
| 9.03 sn ses → transkripsiyon | 1.09 sn (realtime factor **0.12**) |
| Model yükleme | 16.6 sn, tek seferlik (sonra önbellekte) |
| Sıfır süreli kelime / çakışma | 0 / 0 |
| Kelime süresi p50 / p95 | 240 / 340 ms |
| Kesim hassasiyeti | Örnek hassasiyetinde (ölçüldü: 6 kelime = 1660 ms, toplamla birebir) |

## SAPI testinin ölçmediği

TTS sesi gerçek konuşmadan temiz: sabit tempo, net kelime araları, gürültü yok.
Ton da düz, o yüzden `tone.py`'ı anlamlı test etmiyor.

Gerçek materyalde iki şeyi ölçmen gerekiyor:

1. **Hizalama** — kesimi dinle, kelime ortasından kesiyor mu
2. **Ton etiketleri** — Gemini'nin "calm" dediği take gerçekten sakin mi

İkisi de ürünün temel iddiası. Doğrulanmadan ilerlemeye değmez.
