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

  // --- Asistan sonucunun uygulanması ---
  // Sayfa içi sohbet ile WebMCP araçları aynı timeline'a yazıyor. Kural store'da
  // tek yerde durduğu için iki giriş kapısı ayrışamıyor.
  console.log("\n=== asistan sonucu ===");
  const agentCandidates = [
    candidate("S01_T03:1:0", 0, 1340),
    candidate("S01_T01:1:0", 0, 820),
  ];

  const applied = store.applyAgentResult({
    candidates: agentCandidates,
    proposal: ["S01_T01:1:0", "S01_T03:1:0"],
  });
  check("adaylar yazıldı", applied.candidates, 2);
  check("öneri uygulandı", applied.segments, 2);
  check("düşen kimlik yok", applied.dropped, []);
  check("ajanın verdiği sıra korundu", store.getState().timeline[0].take_id, "S01_T01");

  // Çözülemeyen kimlik sessizce yutulmamalı
  const partial = store.applyAgentResult({
    candidates: agentCandidates,
    proposal: ["S01_T03:1:0", "hayalet:9:9"],
  });
  check("çözülemeyen kimlik bildirildi", partial.dropped, ["hayalet:9:9"]);
  check("çözülenler yine uygulandı", store.getState().timeline.length, 1);

  // Öneri yoksa timeline'a dokunulmamalı
  store.setTimeline(agentCandidates);
  store.applyAgentResult({ candidates: agentCandidates, proposal: [] });
  check("önerisiz cevap timeline'ı bozmadı", store.getState().timeline.length, 2);

  store.clearTimeline();
  store.patch({ candidates: [] });

  console.log("\n=== sohbet durumu ===");
  store.setChat({ available: false, reason: "anahtar yok" });
  check("sohbet kapalı", store.getState().chat.available, false);
  store.appendChatMessage({ role: "user", text: "merhaba" });
  store.appendChatMessage({ role: "agent", text: "buldum", toolCalls: [{ name: "find_line" }] });
  check("iki mesaj birikti", store.getState().chat.messages.length, 2);
  check(
    "araç izi korundu",
    store.getState().chat.messages[1].toolCalls[0].name,
    "find_line"
  );
  store.setChat({ messages: [] });
}

// --- WebMCP araçları -------------------------------------------------------
// Sahte bir modelContext ile test ediyoruz. Geçen projede doğrulanan yüzey:
// registerTool(tool, {signal}), aynı isme ikinci kayıt reddedilir, signal abort
// edilince kayıt düşer. `updateTool` diye bir API yok.
console.log("\n=== WebMCP araçları ===");
{
  const { installTools } = await import("./src/webmcp.js");
  const store = await import("./src/store.js");
  store.clearTimeline();

  function fakeModelContext() {
    const tools = new Map();
    const rejected = [];
    return {
      tools,
      rejected,
      async registerTool(tool, { signal } = {}) {
        if (tools.has(tool.name)) {
          const error = new Error(`Tool '${tool.name}' is already registered.`);
          rejected.push(tool.name);
          throw error;
        }
        tools.set(tool.name, tool);
        signal?.addEventListener("abort", () => tools.delete(tool.name), { once: true });
      },
      async getTools() {
        return [...tools.values()].map(({ execute, ...rest }) => rest);
      },
    };
  }

  const searchCalls = [];
  const candidate = (id, tone, start, end) => ({
    id,
    rank: 1,
    take_id: id.split(":")[0],
    line_id: 1,
    scene: "S01",
    camera: "A",
    speaker: "MAYA",
    tone,
    tone_score: 0.9,
    start_ms: start,
    end_ms: end,
    duration_ms: end - start,
    text: "I never asked for this",
    source_url: `${id.split(":")[0]}.wav`,
    media_url: `/media/${id.split(":")[0]}.wav`,
  });

  const pool = [
    candidate("S01_T03:1:0", "calm", 0, 1340),
    candidate("S01_T01:1:0", "tense", 0, 820),
  ];

  const actions = {
    async search({ phrase, tone }) {
      searchCalls.push({ phrase, tone });
      const found = tone ? pool.filter((item) => item.tone === tone) : pool;
      store.patch({ candidates: found, query: { phrase, tone } });
      return { phrase, tone: tone || null, total: found.length, candidates: found };
    },
    propose(candidates) {
      return store.setTimeline(candidates);
    },
    async play() {
      return true;
    },
    async preview() {
      return true;
    },
    async render() {
      return { job_id: "job-1" };
    },
  };

  store.patch({
    session: {
      role: "guest",
      credits: 10,
      costs: { find_line: 0, propose_cut: 0, preview_segment: 0, commit_render: 1 },
    },
  });

  const context = fakeModelContext();
  const install = await installTools({ actions, store, modelContext: context });

  check("beş araç kaydedildi", install.registered.length, 5);
  check(
    "araç isimleri",
    [...context.tools.keys()].sort(),
    ["commit_render", "find_line", "get_timeline_state", "preview_segment", "propose_cut"]
  );
  check("hiç kayıt reddedilmedi", context.rejected, []);
  check("store durumu güncellendi", store.getState().webmcp, { available: true, registered: 5 });

  const namePattern = /^[A-Za-z0-9_.-]{1,128}$/;
  checkThat(
    "isimler spec desenine uyuyor",
    [...context.tools.keys()].every((name) => namePattern.test(name))
  );
  checkThat(
    "her araç açıklama ve şema taşıyor",
    [...context.tools.values()].every(
      (tool) =>
        typeof tool.description === "string" &&
        tool.description.trim().length > 20 &&
        tool.inputSchema?.type === "object" &&
        typeof tool.execute === "function"
    )
  );
  check(
    "salt okunur araçlar işaretli",
    ["find_line", "get_timeline_state"].map(
      (name) => context.tools.get(name).annotations.readOnlyHint
    ),
    [true, true]
  );
  check(
    "yazan araçlar salt okunur değil",
    ["propose_cut", "commit_render"].map(
      (name) => context.tools.get(name).annotations.readOnlyHint
    ),
    [false, false]
  );

  // --- execute yolları ---
  const found = await context.tools.get("find_line").execute({ phrase: "I never asked for this" });
  check("find_line arama yaptı", searchCalls.at(-1), {
    phrase: "I never asked for this",
    tone: "",
  });
  check("find_line iki aday döndü", found.candidates.length, 2);
  checkThat("find_line özet cümlesi var", found.summary.includes("2 take(s)"), found.summary);
  checkThat(
    "aday provenance taşıyor",
    found.candidates[0].source === "S01_T03.wav",
    JSON.stringify(found.candidates[0])
  );

  const toneFiltered = await context.tools
    .get("find_line")
    .execute({ phrase: "I never asked for this", tone: "calm" });
  check("ton filtresi tek aday", toneFiltered.candidates.length, 1);

  // Aday havuzunu geri yükle
  await context.tools.get("find_line").execute({ phrase: "I never asked for this" });

  const proposed = await context.tools
    .get("propose_cut")
    .execute({ candidate_ids: ["S01_T03:1:0", "S01_T01:1:0"] });
  check("propose_cut iki parça koydu", proposed.segments.length, 2);
  check("propose_cut render etmedi", proposed.rendered, false);
  check("timeline store'a yazıldı", store.getState().timeline.length, 2);
  check("sıra korundu", store.getState().timeline[0].take_id, "S01_T03");
  check("toplam süre", proposed.total_duration_ms, 1340 + 820);

  // Bilinmeyen id sessizce yutulmamalı, ajana ne yapacağını söylemeli
  let proposeError = null;
  try {
    await context.tools.get("propose_cut").execute({ candidate_ids: ["yok:1:0"] });
  } catch (error) {
    proposeError = error.message;
  }
  checkThat("bilinmeyen id reddedildi", proposeError !== null);
  checkThat(
    "hata mesajı yol gösteriyor",
    proposeError?.includes("find_line") && proposeError?.includes("yok:1:0"),
    proposeError
  );

  let emptyError = null;
  try {
    await context.tools.get("propose_cut").execute({ candidate_ids: [] });
  } catch (error) {
    emptyError = error.message;
  }
  checkThat("boş liste reddedildi", emptyError?.includes("at least one"), emptyError);

  // get_timeline_state insanın değişikliğini görmeli — HITL döngüsünün kanıtı
  store.removeFromTimeline(0);
  const readBack = await context.tools.get("get_timeline_state").execute();
  check("insanın çıkardığı parça yansıdı", readBack.segments.length, 1);
  check("kalan doğru parça", readBack.segments[0].take, "S01_T01");
  check("hâlâ render edilmedi", readBack.rendered, false);

  const previewed = await context.tools
    .get("preview_segment")
    .execute({ candidate_id: "S01_T03:1:0" });
  checkThat("önizleme render etmiyor", previewed.summary.includes("Nothing was rendered"), previewed.summary);

  // --- Duruma göre açıklama güncellemesi ---
  // Kredi bitince ajan çağırmadan önce öğrenmeli.
  const before = context.tools.get("commit_render").description;
  checkThat("kredi varken kullanılabilir", before.includes("10 left"), before);

  store.patch({
    session: { ...store.getState().session, credits: 0 },
  });
  await install.sync();

  const after = context.tools.get("commit_render").description;
  checkThat("kredi bitince UNAVAILABLE yazıyor", after.includes("UNAVAILABLE"), after);
  checkThat(
    "arama hâlâ çalışıyor deniyor",
    after.includes("cost nothing"),
    after
  );
  check("yeniden kayıtta çoğalma yok", context.tools.size, 5);
  check("çift kayıt denemesi olmadı", context.rejected, []);
  checkThat(
    "bedava araçlar etkilenmedi",
    !context.tools.get("find_line").description.includes("UNAVAILABLE")
  );

  // --- WebMCP olmayan tarayıcı ---
  const withoutContext = await installTools({ actions, store, modelContext: null });
  check("WebMCP yoksa patlamıyor", withoutContext.available, false);
  check("durum kapalı işaretlendi", store.getState().webmcp.available, false);
}

const passed = results.filter(Boolean).length;
console.log(`\n${passed}/${results.length} test geçti`);
process.exit(passed === results.length ? 0 : 1);
