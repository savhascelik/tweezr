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

Bu depo yapım aşamasında. Bugün itibarıyla **çalışan ve ölçülmüş** kısım:

- Medyadan kelime bazlı zaman kodu çıkarma, CPU'da, gerçek zamanın 8 katı hızda
- ClickHouse'a ingest ve kelime/cümle araması, ton filtresiyle
- Kelime sınırından örnek hassasiyetinde kesim ve birleştirme
- Farklı kayıtlardan tek tek kelime toplayıp yeni cümle kurma
- Gemini ton sınıflandırması (kod hazır, canlı anahtarla henüz doğrulanmadı)

Henüz **yok**: web arayüzü, timeline, WebMCP araçları, ADK ajanı, render.
Bkz. `pipeline/README.md`.

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

## Kurulum

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r pipeline\requirements.txt
docker compose -f dev\docker-compose.yml up -d
Copy-Item .env.example .env    # sonra doldur
```

Uçtan uca çalıştırma ve doğrulama: `pipeline/README.md`.

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
