# web — timeline ve sanal kırpma

Kurgucunun çalıştığı ekran. Build adımı yok: servis edilen dosya kaynağın kendisi.

## Neden bundler yok

Bu arayüzde React'in ya da bir bundler'ın çözdüğü bir problem yok. İhtiyaç duyulan tek
şey WebMCP araçlarının değiştirdiği durumu yansıtan bir store, o da `store.js`'deki
40 satır. Karşılığında kazanılanlar:

- Cloud Run imajında node aşaması yok, deploy riskinin bir kategorisi kalkıyor
- npm tedarik zinciri yüzeyi sıfır
- Depoyu okuyan jüri servis edilen dosyanın aynısını görüyor

Blueprint React diyordu; bu bilinçli bir sapma.

## Dosyalar

| Dosya | İş |
| --- | --- |
| `index.html` | Tek kök. Inline script yok. |
| `styles.css` | Kurgu odası paleti: koyu, düşük doygunluk, tek vurgu rengi. |
| `src/store.js` | Tek durum kaynağı. Araçlar ve panel aynı store'u değiştiriyor. |
| `src/api.js` | Sunucu çağrıları. Oturum çerezi HttpOnly, fetch otomatik gönderiyor. |
| `src/player.js` | Sanal kırpma oynatıcısı. Çift tampon + rAF. |
| `src/ui.js` | Render. `el()` yardımcısı sadece `textContent` kabul ediyor. |
| `src/webmcp.js` | Beş WebMCP aracı. Kayıt, durum yansıması, fallback. |
| `src/approve.js` | Render onay penceresi. Closed shadow root. |
| `src/main.js` | Bağlama + `ready` promise'i. |
| `test_web.mjs` | Store, WebMCP ve enjeksiyon testleri. |
| `test_approve.mjs` | Onay penceresi testleri, sahte DOM ile. |

## Onay penceresi

Ürünün tek geri alınamaz adımının kapısı. Ajan `commit_render` çağırdığında pencere
açılıyor ve **aracın promise'i insanın kararını bekliyor** — HITL kapısının somut hali.
Pencere render'ı kimin istediğini de yazıyor.

Üç savunma, her birinin somut bir sebebi var:

**Closed shadow root.** Sayfadaki başka bir script `host.shadowRoot` ile içeriye
ulaşamıyor (closed'da `null` dönüyor), yani Approve düğmesini bulup programatik olarak
basamıyor.

**Sadece `textContent`.** Geçen projede onay penceresini `innerHTML` ile kurmuştuk ve
zehirli bir araç adı kendi Approve düğmesine basabiliyordu. Buradaki metinler take
kimlikleri, replik metni ve dosya adları — hepsi kontrol etmediğimiz veri.

**Host stilleri inline ve `!important`.** Sayfa CSS'i pencereyi görünmez yapıp
kullanıcıya farkında olmadan onaylatamasın.

İki küçük ama önemli detay: varsayılan odak **Vazgeç**'te, yanlışlıkla Enter'a basmak
render başlatmasın. Ve zaman aşımı (5 dk) **red** yönünde çözülüyor — ajan çağırıp insan
masadan kalkarsa aracın promise'i sonsuza beklemesin, ama sessizce onaylanmasın da.

## WebMCP araçları

Klasik ajan kurgusunda ajan backend'e "şu aralıkları kes, render et" der ve MP4 geri
döner. Yanlış take seçtiyse bunu render bittikten sonra anlarsın; backend kapalı bir
kutudur. Burada araçlar sayfada çalışıyor, yani öneri kurgucunun ekranında, gerçek
medyanın üstünde, her fragmentin kaynağı görünür halde beliriyor. Karar render'dan
**önce** veriliyor.

| Araç | İş | Kredi |
| --- | --- | --- |
| `find_line` | Repliği ara, sıralı aday döndür. Sonuçlar ekranda da görünüyor. | 0 |
| `propose_cut` | Öneriyi timeline'a koy. Render yok, insan değiştirebilir. | 0 |
| `preview_segment` | Adayı ya da tüm öneriyi çal. | 0 |
| `get_timeline_state` | Timeline'ı oku — **insanın yaptığı değişiklikler dahil**. | 0 |
| `commit_render` | Onaydan sonra dosya üret. | 1 |

`get_timeline_state`'in açıklaması ajana insanın öneriyi değiştirmiş olabileceğini
söylüyor ve `commit_render`'dan önce okumasını istiyor. HITL döngüsünü açık eden şey bu.

### Doğrulanmış API yüzeyi

Geçen projede gerçek bir ChatGPT in-app browser koşusunda test edildi:

```js
document.modelContext.registerTool(tool, { signal })  // native yalnızca navigator'da olabilir
document.modelContext.getTools()                      // execute içermez
document.modelContext.executeTool(toolObject, input)  // isim DEĞİL, obje
```

Araç adı 1-128 karakter, `[A-Za-z0-9_.-]`. `execute` düz JSON objesi döndürüyor.

**`updateTool` diye bir API yok.** Açıklamayı değiştirmek için kaydı `AbortController`
ile iptal edip yeniden kaydetmek gerekiyor — `sync()` bunu yapıyor.

### Kredi bitince ajan çağırmadan öğreniyor

`commit_render`'ın açıklaması kredi durumunu taşıyor. Bakiye yetmezse açıklama
`UNAVAILABLE RIGHT NOW` ile başlıyor ve aramanın hâlâ bedava olduğunu söylüyor. Ajan
görev ortasında hata almak yerine önceden biliyor.

Bu yeniden kayıt **sadece oturum değişince** yapılıyor. Her durum değişiminde yapmak
oynatma sırasında saniyede ~60 kez beş araç tanımı kurmak demekti; rAF döngüsü her
karede `setPlayback` çağırıyor.

### Polyfill yüklemiyoruz

WebMCP yoksa araçlar kaydedilmiyor ve arayüz paneli devrede kalıyor. Ajan desteğini
taklit etmek, desteklemeyen tarayıcıda sessizce yanlış davranış üretir.

Kayıt reddedilirse sebebi konsola yazılıyor. En sık sebep `Origin-Agent-Cluster`
header'ının eksik olması ve `SecurityError` — sunucu bu header'ı veriyor ve
`server/test_api.py` onu test ediyor.

## Sanal kırpma nasıl çalışıyor

Ürünün "render etmeden gör" iddiası `player.js`'de gerçekleşiyor. Bir kaba kurguyu
duymak için hiçbir şey encode edilmiyor; kaynak dosyalarda ileri geri atlanıyor.

İki karar bunu kullanılabilir kılıyor:

**`timeupdate` değil, `requestAnimationFrame`.** `timeupdate` saniyede ~4 kez
tetikleniyor, yani kesim noktasını 250 ms'e kadar kaçırabilir. Biz kelime sınırından
kesiyoruz — 250 ms sonraki kelimeyi de duyurur. rAF ~16 ms veriyor.

**Çift tampon.** Tek element kullanıp her segmentte `src`/`currentTime` değiştirmek
geçişlerde yükleme boşluğu bırakıyor. İki `<video>` dönüşümlü çalışıyor: biri çalarken
diğeri sıradaki segmente konumlanıyor, geçiş anında sadece `play()` çağrılıyor.

Medya sunucusunun **HTTP range** desteklemesi şart. Doğrulandı: `/media/*` 206 ve
`Content-Range` dönüyor. Olmasa her arama dosyanın tamamını indirirdi.

## Provenance şeridi

Timeline'daki her parçanın altında hangi take, hangi sahne, hangi kamera, hangi
konuşmacı, hangi ton ve hangi milisaniye aralığı yazıyor. "kaynağı aç" bağlantısı
medya fragment'i (`#t=start,end`) ile tam o aralığı açıyor.

Bu şerit kurgucu faydasından fazlası: ürünün **"söylemediğini söylettim" değil,
"söylediklerini kaynağıyla bir araya getirdim"** olmasını sağlayan şey. Birincisi
deepfake aracı, ikincisi gazetecilik. Aradaki fark bu şerit.

## HTML enjeksiyon disiplini

Hiçbir yerde `innerHTML` yok. `el()` yardımcısı bilerek sadece `textContent` kabul
ediyor.

Sebep somut: geçen projede onay penceresini `innerHTML` ile kurmuştuk ve zehirli bir
araç adı kendi Approve düğmesine basabiliyordu. Buradaki metinlerin kaynağı Whisper
çıktısı ve kullanıcı dosya adları — yani kontrol etmediğimiz veri.

`test_web.mjs` bunu her koşuda kontrol ediyor ve kontrolün kendisini de test ediyor
(gerçek kullanımı yakalıyor mu, yorumlarda yanlış pozitif üretiyor mu).

## Test

```powershell
node web\test_web.mjs
node web\test_approve.mjs
```

`test_approve.mjs` 28 test, minimal bir sahte DOM ile: shadow root'un `closed`
açılması, host stillerinin zorlanması, `role=dialog`/`aria-modal`, provenance metninin
görünmesi, varsayılan odağın Vazgeç'te olması, Escape'in reddetmesi, zaman aşımının
**red** yönünde çözülmesi, ajan isteğinin ayrıca işaretlenmesi, dinleyicilerin
bırakılmaması ve çift karara karşı korunma.

`test_web.mjs` 67 test. Enjeksiyon disiplini, `el()` sözleşmesi, dış bağlantı `noopener`'ı, store
timeline işlemleri, `getState`'in kopya döndürmesi, abonelik yaşam döngüsü ve bir
abonenin hatasının diğerlerini düşürmemesi.

WebMCP tarafı sahte bir `modelContext` ile test ediliyor: beş aracın kaydı, isim
deseni, şemalar, `readOnlyHint` işaretleri, `execute` yolları, bilinmeyen aday
kimliğinde ajana yol gösteren hata, `get_timeline_state`'in insanın çıkardığı parçayı
yansıtması, kredi bitince açıklamanın değişmesi, yeniden kayıtta çoğalma olmaması ve
WebMCP olmayan tarayıcıda sessizce fallback'e düşmesi.

Bu testler iki gerçek hatayı yakaladı: `sync()` uçuştaki bir senkronizasyonu
beklemeden dönüyordu (yani `await sync()` oturmuş duruma bakmayı garanti etmiyordu),
ve abone her durum değişiminde yeniden kayıt tetikliyordu.

## Tarayıcıda elle doğrulanması gerekenler

`jsdom` medya oynatmayı uygulamıyor — `currentTime` ilerlemiyor, `play()` çalışmıyor.
Yani şunlar otomatik test edilemiyor ve elle bakılmalı:

1. Öneriyi oynat: parçalar sırayla çalıyor mu, geçişte boşluk veya tık var mı
2. Kesim noktaları: kelimenin ortasından kesiliyor mu
3. Aktif parça timeline'da vurgulanıyor mu, sayaç ilerliyor mu
4. "kaynağı aç" doğru aralığı açıyor mu
5. Önizle tek parçayı çalıp duruyor mu
6. Onay penceresi gerçek shadow DOM'da beklendiği gibi görünüyor ve okunuyor mu
7. Render sonrası indirme bağlantısı çalışıyor mu
