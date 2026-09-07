/**
 * Frontend testleri. Bağımlılık yok, düz node.
 *
 *   node web/test_web.mjs
 *
 * Tarayıcı gerektiren şeyler (rAF döngüsü, çift tampon geçişi, medya arama) burada
 * DEĞİL — jsdom medya oynatmayı uygulamıyor, currentTime ilerlemiyor. Onlar elle
 * doğrulanıyor. Buradaki testler saf mantık ve statik disiplin.
 */

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const results = [];

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  results.push(ok);
  console.log(`  ${ok ? "GEÇTİ" : "BAŞARISIZ"}      ${name}`);
  if (!ok) {
    console.log(`               beklenen: ${JSON.stringify(expected)}`);
    console.log(`               gelen   : ${JSON.stringify(actual)}`);
  }
}

function checkThat(name, condition, detail = "") {
  results.push(Boolean(condition));
  console.log(`  ${condition ? "GEÇTİ" : "BAŞARISIZ"}      ${name}`);
  if (!condition && detail) console.log(`               ${detail}`);
}

function sourceFiles() {
  const dir = join(here, "src");
  return readdirSync(dir)
    .filter((name) => name.endsWith(".js"))
    .map((name) => ({ name, body: readFileSync(join(dir, name), "utf8") }));
}

// --- Statik disiplin ---------------------------------------------------------
// Geçen projede onay penceresini innerHTML ile kurmuştuk ve zehirli bir araç adı
// kendi Approve düğmesine basabiliyordu. Bu test o sınıf hatanın geri gelmesini
// engelliyor: metinlerin kaynağı Whisper çıktısı ve dosya adları, yani
// kontrol etmediğimiz veri.
console.log("=== HTML enjeksiyon disiplini ===");
{
  // Yorumları atıyoruz: bu dosyalar neden innerHTML kullanmadıklarını ANLATIYOR,
  // yani kelime yorumlarda geçiyor. Aranan şey gerçek kullanım.
  const stripComments = (body) =>
    body
      .replace(/\/\*[\s\S]*?\*\//g, "")   // blok yorumlar
      .replace(/(^|[^:"'`\w])\/\/.*$/gm, "$1"); // satır yorumları, http:// değil

  // Nokta ile eşleştiriyoruz: tehlikeli kullanım her zaman `bir_şey.innerHTML`.
  const banned = [
    /\.(innerHTML|outerHTML|insertAdjacentHTML)\b/,
    /document\s*\.\s*write\s*\(/,
  ];

  for (const { name, body } of sourceFiles()) {
    const code = stripComments(body);
    const found = banned.filter((pattern) => pattern.test(code)).map(String);
    checkThat(`${name} HTML enjeksiyonu yapmıyor`, found.length === 0, `bulundu: ${found}`);
  }

  // Testin gerçekten bir şey ölçtüğünü doğrula: kasten bozuk girdi yakalanmalı
  checkThat(
    "test gerçek kullanımı yakalıyor",
    banned.some((pattern) => pattern.test('node.innerHTML = data')),
    "kontrol kendi kendini doğrulamıyor"
  );
  checkThat(
    "test yorumları yakalamıyor",
    !banned.some((pattern) => pattern.test(stripComments("// innerHTML asla"))),
    "yorumlar yanlış pozitif üretiyor"
  );

  const ui = sourceFiles().find((file) => file.name === "ui.js").body;
  checkThat(
    "el() yardımcısı textContent kullanıyor",
    ui.includes("node.textContent = value"),
    "el() metni textContent ile yazmalı"
  );
  checkThat(
    "dış bağlantılar noopener taşıyor",
    ui.includes('rel: "noopener noreferrer"'),
    "target=_blank bağlantıları noopener olmadan açılmamalı"
  );
}

// --- store.js saf mantığı ----------------------------------------------------
console.log("\n=== store ===");
{
  // structuredClone node 24'te global
  const store = await import("./src/store.js");

  const candidate = (id, start, end, extra = {}) => ({
    id,
    take_id: id.split(":")[0],
    line_id: 1,
    start_ms: start,
    end_ms: end,
    text: "I never asked for this",
    media_url: `/media/${id.split(":")[0]}.wav`,
    source_url: `${id.split(":")[0]}.wav`,
    ...extra,
  });

  check("başlangıçta timeline boş", store.getState().timeline, []);

  store.appendToTimeline(candidate("S01_T01:1:0", 0, 820));
  store.appendToTimeline(candidate("S01_T03:1:0", 0, 1340));
  check("iki parça eklendi", store.getState().timeline.length, 2);
  check("toplam süre", store.timelineDurationMs(), 820 + 1340);

  const first = store.getState().timeline[0];
  check("süre türetildi", first.duration_ms, 820);
  check("ton varsayılanı", first.tone, "neutral");

  store.removeFromTimeline(0);
  check("parça çıkarıldı", store.getState().timeline.length, 1);
  check("doğru parça kaldı", store.getState().timeline[0].take_id, "S01_T03");

  // propose_cut aracının yaptığı şey: tüm timeline'ı değiştirmek
  store.setTimeline([candidate("S01_T05:1:0", 100, 1300)]);
  check("öneri timeline'ı değiştirdi", store.getState().timeline.length, 1);
  check("öneri doğru parça", store.getState().timeline[0].take_id, "S01_T05");

  // getState kopya döndürmeli, yoksa dışarıdan mutasyon store'u sessizce bozar
  const snapshot = store.getState();
  snapshot.timeline.push(candidate("HACK:1:0", 0, 1));
  check("getState kopya döndürüyor", store.getState().timeline.length, 1);

  let notified = 0;
  const unsubscribe = store.subscribe(() => (notified += 1));
  store.setStatus("ok", "test");
  check("abone haber aldı", notified, 1);
  unsubscribe();
  store.setStatus("idle", "");
  check("abonelik iptal edildi", notified, 1);

  // Bir abonenin hatası diğerlerini düşürmemeli
  let second = 0;
  const offBad = store.subscribe(() => {
    throw new Error("bozuk abone");
  });
  const offGood = store.subscribe(() => (second += 1));
  store.setStatus("ok", "hata sonrası");
  check("bozuk abone diğerini engellemiyor", second, 1);
  offBad();
  offGood();

  store.clearTimeline();
  check("temizlendi", store.getState().timeline, []);
}

const passed = results.filter(Boolean).length;
console.log(`\n${passed}/${results.length} test geçti`);
process.exit(passed === results.length ? 0 : 1);
