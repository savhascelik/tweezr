# (proje adı buraya)

> Adı sen koyacaksın. Jürinin gördüğü ilk şey o ve yarışma ipucu açıkça
> "projenin adını yapay zekaya koydurma" diyor. `TakeFinder`, `Continuity`,
> `RoughCut`, `Citeline` fikir olsun diye duruyor, hiçbiri bağlayıcı değil.

Bir çekim günü 40 dakikalık sahneden 6 saat görüntü üretir. Aynı replik 12 kez, farklı
tonlarda, farklı açılardan çekilir. Sonra kurgu odasında şu soru sorulur:

> "'I never asked for this' repliğinin daha sakin okunduğu take hangisiydi?"

Bugünün cevabı: eksik script supervisor notları ya da saatlerce görüntü taraması.

Bu araç kütüphanenize bir replik yazdığınızda hangi kayıtta hangi milisaniyede
söylendiğini buluyor, tonuna göre sıralıyor, ve kaba kurguyu **render etmeden**
timeline'da öneriyor. Her parçanın altında hangi kayıt, hangi timecode.

## Şu an ne çalışıyor

Bu depo yapım aşamasında. Bugün itibarıyla **çalışan ve test edilmiş** kısım:

- Medyadan kelime bazlı zaman kodu çıkarma, CPU'da, gerçek zamanın 8 katı hızda
- ClickHouse'a ingest ve kelime/cümle araması, ton filtresiyle — 12 sorgu testi
- Kelime sınırından örnek hassasiyetinde kesim ve birleştirme
- Farklı kayıtlardan tek tek kelime toplayıp yeni cümle kurma
- HTTP API: anonim oturum, kredi defteri, `Origin-Agent-Cluster` header'ı,
  sıralı aday listesi, provenance alanları — 38 API testi
- Timeline arayüzü: sanal kırpma oynatıcı (çift tampon + rAF), tıklanabilir
  provenance şeridi, WebMCP'siz tarayıcı için elle sürülebilir panel
- Beş WebMCP aracı: `find_line`, `propose_cut`, `preview_segment`,
  `get_timeline_state`, `commit_render`
- Onay penceresi: closed shadow root, provenance görünür, varsayılan odak Vazgeç'te
- Render: onaydan sonra FFmpeg birleştirmesi, 1 kredi, oturuma özel indirme —
  canlı ölçüldü: iki farklı take'ten 2160 ms çıktı, beklenenin tam eşi
- Sayfa içi ADK asistanı (anahtar varsa; yoksa ürün onsuz çalışıyor)

Kod hazır ama **doğrulanmadı**: Gemini ton sınıflandırması ve asistan turu (canlı
anahtar yok), araçların gerçek bir ajan istemcisinde görünmesi (yerelde
`document.modelContext` olan tarayıcı yok).

`pipeline/README.md`, `server/README.md` ve `web/README.md` ayrıntıları taşıyor.

## Neden ClickHouse

Kelime bazlı retrieval bir analitik sorgu problemi. Tablo şu şekilde:
her kelime bir satır, primary key `(project_id, word_norm, start_ms)`.

"Bu kelime kütüphanede nerede geçiyor" sorusu doğrudan primary index'ten geliyor.
Cümle araması bunun üstüne ClickHouse'un yüksek mertebeden dizi fonksiyonlarını
koyuyor — `arraySort` + `arrayFilter` + `arraySlice` ile ardışık eşleşme, tek geçiş,
join yok. Bir satırda cümle birden çok kez geçiyorsa hepsi dönüyor.

ClickHouse burada bir metrik kovası değil, **retrieval motorunun kendisi**.
SQL `pipeline/queries.py` içinde, testleri `pipeline/test_queries.py` içinde.

## Neden Whisper ve Gemini birlikte

İki farklı problem türü, iki farklı araç:

**Kelime zaman kodu bir hizalama problemi.** Metni ve sesi biliyorsun, aradaki
eşleşmeyi arıyorsun. Generative bir modelden zaman kodu istemek ona yanlış türde iş
vermek olur. Bu yüzden `faster-whisper`, CPU'da, ücretsiz ve tekrar üretilebilir.

**Ton bir prozodi + anlam problemi.** "Bu take daha sakin" demek Whisper'ın
yapamadığı şey. Gemini multimodal orada devrede, take başına tek çağrı.

## Klasörler

| Dizin | İçerik |
| --- | --- |
| `pipeline/` | Hizalama, ton, ingest, ClickHouse sorguları |
| `server/` | FastAPI: API, oturum, kredi, render, ADK asistanı |
| `web/` | Timeline arayüzü, sanal kırpma, WebMCP araçları, onay penceresi |
| `demo/` | Demo korpusu — **içerik**, imajda bulunmak zorunda |
| `dev/` | Yerel ClickHouse, korpus üretme/yükleme, deploy doğrulama |
| `scratch/` | Üretilen çöp, tamamen gitignore'da |

`demo/` ile `scratch/` arasındaki ayrım kasıtlı: kodun ürettiği şeyi commit etmiyoruz
ama **ürünün gösterdiği şeyi** ediyoruz. Demo medyası imajda olmazsa jüri hiçbir şey
duyamaz.

## Kurulum

Tüm komutlar bu dizinden çalışıyor.

```powershell
python -m venv .venv

# Sadece sunucu (Cloud Run imajının taşıdığı şey)
.venv\Scripts\python.exe -m pip install -r requirements.txt

# Korpus hazırlamak için transkripsiyon yığınını da ekle
.venv\Scripts\python.exe -m pip install -r requirements-ingest.txt

docker compose -f dev\docker-compose.yml up -d
Copy-Item .env.example .env    # sonra doldur
```

Bağımlılıkların ayrı olması bilinçli: `faster-whisper` + `ctranslate2` yüzlerce MB ve
sunucuda hiç çalışmıyor. Transkripsiyon offline yapılıyor, sonuç ClickHouse'a yazılıyor,
sunucu sadece sorguluyor.

## Çalıştırma

```powershell
.venv\Scripts\python.exe -m dev.load_demo          # demo korpusunu ClickHouse'a yaz
.venv\Scripts\python.exe -m uvicorn server.main:app --reload --port 8080
```

Demo korpusunu yeniden üretmek için (Windows SAPI gerekiyor):
`.venv\Scripts\python.exe -m dev.seed_demo`

## Konteyner

```powershell
docker build -t cinema-app:dev .
docker run --rm -p 8090:8080 --network dev_default `
  -e CLICKHOUSE_HOST=clickhouse -e CLICKHOUSE_PORT=8123 `
  -e CLICKHOUSE_PASSWORD=dev -e CLICKHOUSE_DATABASE=cinema cinema-app:dev
```

337 MB, tek aşama. node aşaması yok (build adımı yok) ve `faster-whisper` yok
(transkripsiyon offline). Yerelde uçtan uca doğrulandı: sayfa, arama, medya range
istekleri ve FFmpeg render'ı slim imajda çalışıyor.

Cloud Run'a dağıtım: `DEPLOY.md`.

## Test

```powershell
.venv\Scripts\python.exe -m pipeline.test_queries    # 12  SQL doğruluk testi
.venv\Scripts\python.exe -m server.test_api          # 99  API + render + ajan araçları
.venv\Scripts\python.exe -m doctest pipeline\schema.py
node web\test_web.mjs                                # 67  store, WebMCP, enjeksiyon
node web\test_approve.mjs                            # 28  onay penceresi
```

Uçtan uca ingest ve kesim doğrulaması: `pipeline/README.md`.
API ve güvenlik duruşu: `server/README.md`.

## Kaynak materyal duruşu

Kullanıcı kendi lisanslı kütüphanesini getirir. Bir kurgu yazılımı kullanıcının kestiği
şeyden sorumlu değildir; bir haber editörünün arşiv klibi kesmesi de meşru bir iştir.

Bu deponun demo korpusu kamu malı kaynaklardan geliyor. Araç üçüncü taraf platformlardan
içerik **indirmiyor** — kullanıcının kendi dosyalarını işliyor.

Her fragmentin kaynağı ve timecode'u saklanıyor ve arayüzde tıklanabilir olacak. Amaç
sadece kurgucu faydası değil: bu araç "söylemediği şeyi söylettim" değil,
**"söylediklerini kaynağıyla bir araya getirdim"** olmalı. Aradaki fark provenance
şeridi, o yüzden şemada `source_url` en baştan var.

## Lisans

MIT, bkz. `LICENSE`.
