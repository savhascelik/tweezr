# Dağıtım — Cloud Run

Konteyner yerelde uçtan uca doğrulandı: sayfa, arama, medya range istekleri ve render
(FFmpeg dahil) slim imajda çalışıyor. Kalan iş hesap açmak ve deploy etmek.

## Önce bilmen gereken iki sınır

**`--max-instances=1` şart.** Render işleri bellekte tutuluyor ve oturum defteri
`/tmp`'teki SQLite'ta. İki örnek çalışırsa bir örnekte başlatılan render'ın durumu
diğerinde 404 döner. Bu bilinçli bir sadelik kararı; bu ölçekte kalıcı kuyruk kurmak
gereksiz karmaşıklık. Trafik beklenmiyorsa tek örnek fazlasıyla yeter.

**`--allow-unauthenticated` gerekiyor** ve bu kasıtlı. Zorunlu giriş WebMCP keşfini
öldürüyor: araçlar sayfa JS'i tarafından kaydediliyor, jüri login duvarı görürse ajan
sıfır araç görür. Karşılığındaki frenler `server/README.md`'de listeli — oturum başına
kredi, IP başına oturum limiti, proje allowlist'i, parametreli sorgular.

## 1. ClickHouse Cloud

Servis oluştur, bağlantı bilgilerini al, sonra yerelden tabloyu kurup korpusu yükle:

```powershell
$env:CLICKHOUSE_HOST     = "xxx.clickhouse.cloud"
$env:CLICKHOUSE_PORT     = "8443"
$env:CLICKHOUSE_SECURE   = "1"
$env:CLICKHOUSE_USER     = "default"
$env:CLICKHOUSE_PASSWORD = "..."
$env:CLICKHOUSE_DATABASE = "cinema"

.venv\Scripts\python.exe -m dev.load_demo
```

`load_demo` tabloyu kuruyor, `demo/takes/*.json`'ı yazıyor ve aramayı doğruluyor.
Whisper çalıştırmıyor, GPU istemiyor.

Kendi çekimini eklediysen önce korpusu üret (bu ağır olan, geliştirici makinesinde):

```powershell
.venv\Scripts\python.exe -m pipeline.transcribe <dosya> --take-id T09 ... --out demo\takes\T09.json
.venv\Scripts\python.exe -m pipeline.tone demo\takes\T09.json --media demo\media\T09.wav
Copy-Item <dosya> demo\media\
.venv\Scripts\python.exe -m dev.load_demo
```

## 2. Cloud Run

```powershell
$PROJECT = "<gcp-proje-id>"
$REGION  = "europe-west1"
$SERVICE = "cinema"

gcloud config set project $PROJECT
gcloud services enable run.googleapis.com artifactregistry.googleapis.com

gcloud run deploy $SERVICE `
  --source . `
  --region $REGION `
  --allow-unauthenticated `
  --max-instances=1 `
  --min-instances=1 `
  --cpu=2 --memory=2Gi `
  --timeout=300 `
  --set-env-vars="CLICKHOUSE_HOST=$env:CLICKHOUSE_HOST,CLICKHOUSE_PORT=8443,CLICKHOUSE_SECURE=1,CLICKHOUSE_USER=default,CLICKHOUSE_DATABASE=cinema" `
  --set-secrets="CLICKHOUSE_PASSWORD=clickhouse-password:latest,GEMINI_API_KEY=gemini-key:latest"
```

Notlar:

- **`--min-instances=1`**: jüri ilk açtığında cold start beklemesin. Boşta duran örnek
  para yakıyor, teslimden sonra `0`'a çek.
- **`--cpu=2`**: FFmpeg concat CPU işi. 1 CPU'da da çalışır, sadece yavaşlar.
- **Şifreler `--set-env-vars` ile DEĞİL `--set-secrets` ile.** Ortam değişkeni olarak
  verilen sır `gcloud run services describe` çıktısında ve konsol arayüzünde düz metin
  görünüyor. Secret Manager'a koy:

```powershell
"..." | gcloud secrets create clickhouse-password --data-file=-
"..." | gcloud secrets create gemini-key --data-file=-
```

- **`GEMINI_API_KEY` opsiyonel.** Vermezsen sayfa içi asistan kapanıyor ve sebebini
  arayüzde yazıyor; arama, timeline, önizleme, provenance ve WebMCP araçları çalışmaya
  devam ediyor.

## 3. Doğrula

```powershell
.venv\Scripts\python.exe -m dev.check_deploy https://<servis-url>
```

25 kontrol: WebMCP ön koşulları (https, `Origin-Agent-Cluster: ?1`, araçları kıracak
CSP yok), varlıklar, oturumun giriş istemeden kurulması, kütüphane ve arama, medyanın
HTTP range desteği, ve gerçek bir render.

### Buradan sonrası elle — ve en önemlisi bu

Script `registerTool`'un başarılı olduğunu **doğrulayamaz**; o bir tarayıcı işi.

1. URL'yi ChatGPT in-app browser'da ya da WebMCP açık Chrome'da aç
2. Ajana araçlarını sor. Beş araç görünmeli: `find_line`, `propose_cut`,
   `preview_segment`, `get_timeline_state`, `commit_render`
3. "I never asked for this repliğinin en sakin okunduğu take'i bul" de
4. Timeline'da öneri çıkmalı, her parçanın altında provenance şeridi olmalı
5. Render istettir: onay penceresi açılmalı ve ajan pencerede beklemeli

Araçlar görünmüyorsa ilk bakılacak yer **tarayıcı konsolu**. `registerTool`
reddedildiyse sebebi orada yazıyor; en sık sebep `Origin-Agent-Cluster` eksikliği ve
`SecurityError` — ama sunucu o header'ı veriyor ve `check_deploy` bunu kontrol ediyor,
yani başka bir şeyse konsol söyleyecek.

## 4. Bütçe freni

Blueprint'te söz verilen global bütçe freni **henüz yok**. Şu an var olanlar: oturum
başına kredi, IP başına oturum limiti, sohbet mesajı sayacı, `MAX_OUTPUT_MS`,
`MAX_UPLOAD_*` ve tek örnek sınırı.

Bunlar kötüye kullanımı sınırlıyor ama Google Cloud faturasını sınırlamıyor. Teslimden
önce yapılması gereken:

- GCP'de **bütçe uyarısı** kur (Billing → Budgets & alerts)
- Teslim ve değerlendirme bitince `--min-instances=0`
- Hackathon bittiğinde servisi sil: `gcloud run services delete $SERVICE`
