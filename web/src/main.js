/**
 * Bağlama noktası.
 *
 * `ready` promise'i dışa açılıyor: WebMCP araç kaydı (task 4) oturum ve kütüphane
 * hazır olduktan SONRA yapılmak zorunda. Geçen projede bunu atlayınca kayıt yarışı
 * yüzünden ajan boş araç listesi görmüştü.
 */

import * as api from "./api.js";
import { confirmRender } from "./approve.js";
import { createPlayer } from "./player.js";
import * as store from "./store.js";
import { createUI } from "./ui.js";
import { installTools } from "./webmcp.js";

const root = document.getElementById("app");

const actions = {
  async search({ phrase, tone }) {
    const trimmed = (phrase ?? "").trim();
    if (!trimmed) {
      store.setStatus("warn", "Aranacak bir replik yaz.");
      return { candidates: [], total: 0 };
    }

    store.patch({ query: { phrase: trimmed, tone: tone ?? "" } });
    store.setStatus("busy", "Aranıyor…");
    try {
      const result = await api.findLine({ phrase: trimmed, tone });
      store.patch({ candidates: result.candidates, session: result.session });
      store.setStatus(
        result.total ? "ok" : "warn",
        result.total
          ? `${result.total} eşleşme. Arama kredi harcamıyor.`
          : `"${result.phrase}" kütüphanede bulunamadı.`
      );
      return result;
    } catch (error) {
      store.setStatus("error", error.message);
      throw error;
    }
  },

  add(candidate) {
    const timeline = store.appendToTimeline(candidate);
    store.setStatus("ok", `${candidate.take_id} eklendi. ${timeline.length} parça.`);
    return timeline;
  },

  propose(candidates) {
    const timeline = store.setTimeline(candidates);
    store.setStatus("ok", `${timeline.length} parçalık öneri hazır. Render edilmedi.`);
    return timeline;
  },

  remove(index) {
    const timeline = store.removeFromTimeline(index);
    store.setStatus("ok", `Parça çıkarıldı. ${timeline.length} kaldı.`);
    return timeline;
  },

  clear() {
    store.clearTimeline();
    player.stop();
    store.setStatus("idle", "");
  },

  play() {
    const { timeline } = store.getState();
    if (!timeline.length) {
      store.setStatus("warn", "Kurgu boş.");
      return;
    }
    store.setPlayback({ playing: true, index: 0, offsetMs: 0 });
    store.setStatus("ok", "Sanal kırpma ile oynatılıyor — hiçbir şey render edilmedi.");
    player.play(timeline);
  },

  preview(candidate) {
    store.setStatus("ok", `${candidate.take_id} önizleniyor.`);
    player.preview(candidate);
  },

  stop() {
    player.stop();
    store.setPlayback({ playing: false, index: -1, offsetMs: 0 });
  },

  /**
   * Sayfa içi asistan. Ajanın bulduğu adaylar ve koyduğu öneri, WebMCP araçlarının
   * kullandığı AYNI store işlemlerinden geçiyor — yani iki giriş kapısı da tek
   * timeline'ı değiştiriyor, insan hangisini kullandığını ayırt etmek zorunda değil.
   */
  async chat(message) {
    store.appendChatMessage({ role: "user", text: message });
    store.setChat({ busy: true });
    try {
      const result = await api.sendChat(message);
      const applied = store.applyAgentResult(result);
      if (applied.dropped.length) {
        console.warn("asistan çözülemeyen aday kimliği döndürdü", applied.dropped);
      }

      store.appendChatMessage({
        role: "agent",
        text: result.reply || "(boş cevap)",
        toolCalls: result.tool_calls ?? [],
      });
      store.setChat({ busy: false, messagesLeft: result.messages_left ?? 0 });
      if (result.session) store.patch({ session: result.session });
      return result;
    } catch (error) {
      store.appendChatMessage({ role: "error", text: error.message });
      store.setChat({ busy: false });
      throw error;
    }
  },

  /**
   * Tek geri alınamaz adım, ve tek kredi harcayan adım.
   *
   * Ajan da insan da buraya geliyor ama onay her ikisinde de insanın. Ajan
   * çağırdığında aracın promise'i pencerede bekliyor — HITL kapısının somut hali.
   */
  async render({ requestedBy = "human" } = {}) {
    const { timeline, session } = store.getState();
    if (!timeline.length) {
      store.setStatus("warn", "Kurgu boş.");
      return { approved: false, reason: "timeline boş" };
    }

    const cost = session?.costs?.commit_render ?? 1;
    const approved = await confirmRender({
      segments: timeline,
      cost,
      credits: session?.credits ?? 0,
      requestedBy,
    });

    if (!approved) {
      store.setStatus("warn", "Render onaylanmadı. Hiçbir şey üretilmedi, kredi harcanmadı.");
      return { approved: false, reason: "insan onaylamadı" };
    }

    store.setStatus("busy", "Render ediliyor…");
    store.patch({ render: { status: "queued", jobId: null, downloadUrl: null } });
    try {
      const queued = await api.requestRender(timeline);
      store.patch({
        render: { status: queued.status, jobId: queued.job_id, downloadUrl: null },
        session: queued.session ?? session,
      });

      const job = await api.waitForRender(queued.job_id);
      if (job.status !== "done") {
        throw new Error(job.error || `Render başarısız (${job.status})`);
      }

      store.patch({
        render: {
          status: "done",
          jobId: job.job_id,
          downloadUrl: job.download_url,
          mode: job.mode,
        },
      });
      store.setStatus(
        "ok",
        `Render bitti: ${job.segments} parça, ${(job.duration_ms / 1000).toFixed(2)} sn. ` +
          `${queued.charged} kredi düştü, ${queued.credits_left} kaldı.`
      );
      return { approved: true, ...job, download_url: job.download_url };
    } catch (error) {
      store.patch({ render: { status: "failed", jobId: null, downloadUrl: null } });
      store.setStatus("error", error.message);
      throw error;
    }
  },
};

const ui = createUI(root, {
  onSearch: (query) => actions.search(query).catch(() => {}),
  onAdd: actions.add,
  onPreview: actions.preview,
  onRemove: actions.remove,
  onClearTimeline: actions.clear,
  onPlay: actions.play,
  onStop: actions.stop,
  onRender: () => actions.render().catch(() => {}),
  onChat: (message) => actions.chat(message).catch(() => {}),
});

const player = createPlayer({
  mount: ui.stage,
  onProgress: ({ index, offsetMs }) => store.setPlayback({ index, offsetMs }),
  onSegmentChange: ({ index }) => store.setPlayback({ playing: true, index, offsetMs: 0 }),
  onEnd: () => {
    store.setPlayback({ playing: false, index: -1, offsetMs: 0 });
    store.setStatus("ok", "Oynatma bitti.");
  },
  onError: (error) => {
    store.setPlayback({ playing: false, index: -1, offsetMs: 0 });
    store.setStatus("error", error.message);
  },
});

store.subscribe((state) => ui.render(state));
ui.render(store.getState());

/**
 * Oturum, kütüphane VE araç kaydı tamamlanınca çözülüyor.
 *
 * Sıra önemli: araçlar oturum ve kütüphane hazır olduktan SONRA kaydediliyor.
 * Geçen projede bunu atlayınca kayıt yarışı yüzünden ajan boş araç listesi görmüştü.
 * Ayrıca araç açıklamaları kredi bakiyesini içeriyor, yani oturum bilinmeden
 * doğru açıklama üretilemez.
 */
export const ready = (async () => {
  try {
    const session = await api.readSession();
    store.patch({ session: session.session });

    const library = await api.libraryStats();
    store.patch({ library: library.stats });

    // Sohbet anahtar yoksa kapalı. Ürünün geri kalanı bundan etkilenmiyor, o yüzden
    // hata olsa bile başlatmayı düşürmüyoruz.
    try {
      const chat = await api.chatStatus();
      store.setChat({
        available: chat.available,
        reason: chat.reason ?? "",
        messagesLeft: chat.messages_left ?? 0,
      });
    } catch (error) {
      store.setChat({ available: false, reason: `Asistan durumu okunamadı: ${error.message}` });
    }

    const webmcp = await installTools({ actions, store });

    store.setStatus(
      "ok",
      webmcp.available
        ? `Hazır. ${library.stats.takes} take yüklü, ${webmcp.registered.length} WebMCP aracı kayıtlı.`
        : `Hazır. ${library.stats.takes} take yüklü. Bu tarayıcıda WebMCP yok, paneli kullan.`
    );
    return { actions, store, player, webmcp };
  } catch (error) {
    store.setStatus(
      "error",
      `Başlatılamadı: ${error.message}. ClickHouse ve sunucu ayakta mı?`
    );
    throw error;
  }
})();

// WebMCP kayıt katmanı (task 4) bunları kullanacak.
window.__cinema = { ready, actions, store, player };
