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
| `src/main.js` | Bağlama + `ready` promise'i. |
| `test_web.mjs` | Testler. Bağımlılık yok, düz node. |

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
```

23 test: enjeksiyon disiplini, `el()` sözleşmesi, dış bağlantı `noopener`'ı, store
timeline işlemleri, `getState`'in kopya döndürmesi, abonelik yaşam döngüsü ve bir
abonenin hatasının diğerlerini düşürmemesi.

## Tarayıcıda elle doğrulanması gerekenler

`jsdom` medya oynatmayı uygulamıyor — `currentTime` ilerlemiyor, `play()` çalışmıyor.
Yani şunlar otomatik test edilemiyor ve elle bakılmalı:

1. Öneriyi oynat: parçalar sırayla çalıyor mu, geçişte boşluk veya tık var mı
2. Kesim noktaları: kelimenin ortasından kesiliyor mu
3. Aktif parça timeline'da vurgulanıyor mu, sayaç ilerliyor mu
4. "kaynağı aç" doğru aralığı açıyor mu
5. Önizle tek parçayı çalıp duruyor mu
