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
| `GET /api/chat/status` | asistan açık mı, kaç mesaj hakkı kaldı | 0 |
| `POST /api/chat` | sayfa içi asistan (ADK) | sayaç |
| `GET /media/*` | medya, HTTP range destekli | 0 |

`find_line` **LLM gerektirmiyor**: cümle → ClickHouse → sıralı aday. Doğal dili araç
parametrelerine çeviren ADK ajanı bunun üstüne biniyor. Yani arama Gemini anahtarı
olmadan da tam çalışıyor.

Sıralama ürün mantığı, SQL'de değil `routes.py`'de: ton skoru yüksek olan önce. Ton
filtresi verildiğinde bu doğrudan "o tonun en iyi örneği önce" oluyor.

`/api/render` bilerek 503 dönüyor. Çalışmayan bir iş için kredi düşürmek sessiz veri
kaybı olur; işçi devreye girene kadar kredi harcanmıyor ve test bunu doğruluyor.

## ADK ajanı neden WebMCP'nin altında değil

Blueprint "WebMCP `find_line` → ADK ajanı" diyordu. Yanlıştı.

Harici ajan (ChatGPT in-app browser) **zaten bir LLM**. `find_line`'ı yapılandırılmış
parametrelerle çağırıyor. Onu bir de bizim ajanımızdan geçirmek, ilk modelin çoktan
yaptığı parametre eşleştirmesini ikinci bir modele yaptırmak olur — gecikme ve hata
yüzeyinden başka bir şey eklemez. O yüzden WebMCP araçları doğrudan API'ye gidiyor ve
**arama Gemini anahtarı olmadan tam çalışıyor.**

ADK ajanının işi başka: **ajanı olmayan kullanıcı.** Çoğu insan ChatGPT in-app
browser'da gezmiyor. Sayfa kendi asistanını taşıyınca ürün harici bir ajan olmadan da
doğal dille kullanılabiliyor, ve iki giriş kapısı da aynı ClickHouse sorgularının
üstünde çalışıyor.

### Üç araç, LLM'siz test edilebilir

`agent_tools.py` içindeki fonksiyonlar saf: `find_line`, `assemble_proposal`,
`get_library_stats`. LLM olmadan çağrılabiliyorlar ve testleri de öyle koşuyor.

İki şey kasıtlı:

**Docstring'ler ajanın gördüğü şemadır.** ADK araç tanımını imza ve docstring'den
üretiyor, yani o metin dokümantasyon değil arayüz. Bu yüzden İngilizce ve fonksiyonun
ne yaptığından çok **ne zaman kullanılacağını** anlatıyorlar.

**Hatalar exception değil sözlük.** `{"error": ..., "allowed_tones": [...]}` dönüyor.
Ajan okuyup düzeltebiliyor; exception ona sadece "başarısız" derdi.

**Öneri tarayıcıya toplayıcı üzerinden dönüyor.** Ajan sunucuda koşuyor, sayfanın
store'una dokunamıyor. Araç niyetini istek başına bir `ContextVar`'a yazıyor, cevap onu
tarayıcıya taşıyor ve sayfa `store.applyAgentResult()` ile uyguluyor — yani harici ajan
ile sayfa içi sohbet aynı timeline'ı aynı yoldan değiştiriyor. `ContextVar` global bir
sözlük olsaydı iki kullanıcının önerisi birbirine bulaşırdı.

`config.DEMO_PROJECT` araçlarda sabit. Bu bir güvenlik özelliği: ajan başka bir projeye
bakmaya ikna edilemiyor.

### Anahtar yoksa

Sohbet kapanıyor, ürün çalışmaya devam ediyor: arama paneli, timeline, önizleme ve
provenance hepsi LLM'siz. `/api/chat/status` sebebi açıkça söylüyor ve arayüz onu
gösteriyor. Bu bilinçli — anahtarsız bir ortamda bile ürünün ne yaptığı görülebilmeli.

### Sohbet krediyle değil sayı ile ölçülüyor

Kredi render ve ingest için. Sohbet bir LLM çağrısı, farklı bir kaynak, ve bedava
bırakmak açık bir LLM ucu demek. `MAX_CHAT_MESSAGES` (40) sayacı `chat_used`
kolonunda ve `consume_chat` kredi düşürmeyle aynı `BEGIN IMMEDIATE` kilidini
kullanıyor — iki eşzamanlı mesaj aynı sayacı okuyup ikisi de geçerse sınır anlamsız
olurdu.

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

70 test: güvenlik header'ları, oturum oluşturma ve korunması, çerez bayrakları, oturum
kimliğinin gövdeye sızmaması, girdi doğrulama, ClickHouse aramasının sıralaması ve
provenance alanları, render'ın kredi harcamaması, kredi defterinin ve sohbet sayacının
atomikliği, sohbetin anahtarsız durumda ne söylediği, ve ajan araçlarının LLM olmadan
tüm yolları.

Atomiklik testleri bakiyenin/sınırın iki üç katı kadar eşzamanlı çağrı yapıp tam
sınır kadarının geçtiğini doğruluyor. Bu olmadan iki eşzamanlı render aynı krediyi iki
kere harcayabilir.

Testler hermetik: kontrat fixture'ını `__api_test__` projesine yazıp sonunda siliyorlar.
Önce "demo"da ne varsa ona bakıyorlardı ve `dev/seed_demo` o veriyi değiştirdiğinde üç
test düşmüştü.
