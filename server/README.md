# server — API ve tek origin

FastAPI. API, medya ve (birazdan) sayfa aynı origin'den servis ediliyor.

## Tek origin neden pazarlık konusu değil

WebMCP'nin üç gereği burada birleşiyor:

1. **Secure context** — Cloud Run HTTPS veriyor.
2. **`Origin-Agent-Cluster: ?1` header'ı ŞART.** Eksikse `registerTool` `SecurityError`
   ile reddediyor ve hiçbir ipucu vermiyor. Geçen projede bunu deploy'da öğrendik, o
   yüzden `server/test_api.py` bu header'ı test ediyor.
3. **Session cookie'si API ile aynı origin'de.** Ajan tarayıcıdan oturum bağlamını
   devralıyor, yani ayrı bir token akışı kurmuyoruz.

CORS yok ve olmayacak: aynı origin'de gereksiz, açmak sadece saldırı yüzeyi ekler.

## Giriş zorunlu değil, ve bu bir tasarım kararı

Araçlar sayfa JavaScript'i tarafından kaydediliyor. Jüri URL'yi açtığında login duvarı
görürse uygulama JS'i hiç çalışmaz, `registerTool` çağrılmaz, ajan **sıfır araç** görür.
Üstüne OAuth redirect akışları ajan güdümlü tarayıcıda kırılgan.

O yüzden: ilk yüklemede anonim oturum, kullanıcı hiçbir şey yapmıyor.

**Bunun bedeli: API kimlik doğrulamasız.** Karşılığında konan frenler:

| Fren | Nerede |
| --- | --- |
| Oturum başına kredi, atomik düşürme | `sessions.charge`, `BEGIN IMMEDIATE` |
| IP başına saatlik yeni oturum limiti | `config.MAX_SESSIONS_PER_IP_PER_HOUR` |
| Proje allowlist'i | `routes.validate_project` |
| Ton allowlist'i | `routes.validate_tone` |
| Cümle uzunluğu tavanı | `config.MAX_PHRASE_WORDS` |
| Parametreli sorgular, string interpolasyon yok | `pipeline/queries.py` |
| Sadece okuma yapan uçlar | render dışında hepsi |

## Kredi

Tek cümle: **"wow" yolu bedava, pahalı yol ölçülü.**

| İşlem | Kredi |
| --- | --- |
| `find_line`, `propose_cut`, `preview_segment` | 0 |
| `commit_render` | 1 |
| Kendi medyasını ingest | dakika başına 1 |

Arama bedava, çünkü jürinin ürünün değerini görmesi hiçbir duvara çarpmamalı. Kredi
burada gelir değil, sürpriz faturaya karşı fren.

Defter SQLite'ta. Cloud Run'ın dosya sistemi kalıcı değil, yani yeniden başlatmada guest
oturumları sıfırlanıyor — bu bir hata değil, kabul edilen davranış: ziyaretçi yeni bir
session ve yeni bir kota alıyor.

## Uçlar

| Uç | İş | Kredi |
| --- | --- | --- |
| `GET /healthz` | sağlık | - |
| `GET /api/session` | oturum + bakiye + fiyat listesi (yoksa oluşturur) | 0 |
| `POST /api/find_line` | cümle → sıralı aday listesi | 0 |
| `GET /api/word/{word}` | kelimenin geçtiği yerler (kelime cımbızlama) | 0 |
| `GET /api/library/stats` | kütüphanede ne var | 0 |
| `POST /api/render` | **henüz 503**, kredi harcamıyor | 1 |
| `GET /media/*` | medya, HTTP range destekli | 0 |

`find_line` **LLM gerektirmiyor**: cümle → ClickHouse → sıralı aday. Doğal dili araç
parametrelerine çeviren ADK ajanı bunun üstüne biniyor. Yani arama Gemini anahtarı
olmadan da tam çalışıyor.

Sıralama ürün mantığı, SQL'de değil `routes.py`'de: ton skoru yüksek olan önce. Ton
filtresi verildiğinde bu doğrudan "o tonun en iyi örneği önce" oluyor.

`/api/render` bilerek 503 dönüyor. Çalışmayan bir iş için kredi düşürmek sessiz veri
kaybı olur; işçi devreye girene kadar kredi harcanmıyor ve test bunu doğruluyor.

## Çalıştırma

```powershell
# app/ kökünden
docker compose -f dev\docker-compose.yml up -d
.venv\Scripts\python.exe -m pipeline.ingest pipeline\fixture.json --replace
.venv\Scripts\python.exe -m uvicorn server.main:app --reload --port 8080
```

`http://127.0.0.1:8080/api/docs` OpenAPI arayüzünü veriyor.

## Test

```powershell
.venv\Scripts\python.exe -m server.test_api
```

38 test: güvenlik header'ları, oturum oluşturma ve korunması, çerez bayrakları, oturum
kimliğinin gövdeye sızmaması, girdi doğrulama, ClickHouse aramasının sıralaması ve
provenance alanları, render'ın kredi harcamaması, ve kredi defterinin atomikliği.

Atomiklik testi bakiyenin iki katı kadar eşzamanlı `charge` çağırıyor ve tam bakiye
kadarının geçtiğini doğruluyor. Bu olmadan iki eşzamanlı render aynı krediyi iki kere
harcayabilir.
