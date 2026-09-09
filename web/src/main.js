/**
 * Bağlama noktası.
 *
 * `ready` promise'i dışa açılıyor: WebMCP araç kaydı oturum ve kütüphane hazır
 * olduktan SONRA yapılmak zorunda. Geçen projede bunu atlayınca kayıt yarışı
 * yüzünden ajan boş araç listesi görmüştü. Araç açıklamaları da kredi bakiyesini
 * içeriyor, yani oturum bilinmeden doğru açıklama üretilemez.
 */

import * as api from "./api.js";
import { confirmRender } from "./approve.js";
import { applyDocumentLocale, onLocaleChange, seconds, t } from "./i18n.js";
import { createPlayer } from "./player.js";
import * as store from "./store.js";
import { createUI } from "./ui.js";
import { installTools } from "./webmcp.js";

const root = document.getElementById("app");

applyDocumentLocale();

const actions = {
  async search({ phrase, tone }) {
    const trimmed = (phrase ?? "").trim();
    if (!trimmed) {
      store.setStatus("warn", t("status.needPhrase"));
      return { candidates: [], total: 0 };
    }

    store.patch({ query: { phrase: trimmed, tone: tone ?? "" } });
    store.setStatus("busy", t("status.searching"));
    try {
      const result = await api.findLine({ phrase: trimmed, tone });
      store.patch({ candidates: result.candidates, session: result.session });
      store.setStatus(
        result.total ? "ok" : "warn",
        result.total
          ? t("status.found", { count: result.total })
          : t("status.notFound", { phrase: result.phrase })
      );
      return result;
    } catch (error) {
      store.setStatus("error", error.message);
      throw error;
    }
  },

  add(candidate) {
    const timeline = store.appendToTimeline(candidate);
    store.setStatus(
      "ok",
      t("status.added", { take: candidate.take_id, count: timeline.length })
    );
    return timeline;
  },

  propose(candidates) {
    const timeline = store.setTimeline(candidates);
    store.setStatus("ok", t("status.proposed", { count: timeline.length }));
    return timeline;
  },

  remove(index) {
    const timeline = store.removeFromTimeline(index);
    store.setStatus("ok", t("status.removed", { count: timeline.length }));
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
      store.setStatus("warn", t("status.emptyCut"));
      return;
    }
    store.setPlayback({ playing: true, index: 0, offsetMs: 0 });
    store.setStatus("ok", t("status.playing"));
    player.play(timeline);
  },

  preview(candidate) {
    store.setStatus("ok", t("status.previewing", { take: candidate.take_id }));
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
        console.warn("assistant returned unresolvable candidate ids", applied.dropped);
      }

      store.appendChatMessage({
        role: "agent",
        text: result.reply || t("assistant.emptyReply"),
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
      store.setStatus("warn", t("status.emptyCut"));
      return { approved: false, reason: "the timeline is empty" };
    }

    const cost = session?.costs?.commit_render ?? 1;
    const approved = await confirmRender({
      segments: timeline,
      cost,
      credits: session?.credits ?? 0,
      requestedBy,
    });

    if (!approved) {
      store.setStatus("warn", t("status.renderDeclined"));
      return { approved: false, reason: "the editor declined" };
    }

    store.setStatus("busy", t("status.rendering"));
    store.patch({ render: { status: "queued", jobId: null, downloadUrl: null } });
    try {
      const queued = await api.requestRender(timeline);
      store.patch({
        render: { status: queued.status, jobId: queued.job_id, downloadUrl: null },
        session: queued.session ?? session,
      });

      const job = await api.waitForRender(queued.job_id);
      if (job.status !== "done") {
        throw new Error(job.error || `render failed (${job.status})`);
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
        t("status.renderDone", {
          count: job.segments,
          duration: seconds(job.duration_ms),
          charged: queued.charged,
          left: queued.credits_left,
        })
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
    store.setStatus("ok", t("status.playbackDone"));
  },
  onError: (error) => {
    store.setPlayback({ playing: false, index: -1, offsetMs: 0 });
    store.setStatus("error", error.message);
  },
});

store.subscribe((state) => ui.render(state));

// Dil değişimi tüm metni tazeliyor. Ayrı bir kod yolu yok: `render` sabit
// etiketleri de yazdığı için durum değişimiyle aynı şekilde ilerliyor.
onLocaleChange(() => {
  ui.render(store.getState());
  refreshChatStatus().catch(() => {});
});

ui.render(store.getState());

/**
 * Asistanın kapalı olma sebebini yerelleştiriyor.
 *
 * Sunucu makine okunur bir `reason_code` döndürüyor, metni istemci üretiyor —
 * sunucu kullanıcının dilini bilmiyor ve bilmek zorunda da değil. Bilinmeyen bir
 * kod gelirse sunucunun İngilizce metnine düşüyoruz, boş bırakmıyoruz.
 */
async function refreshChatStatus() {
  const chat = await api.chatStatus();
  const localized = chat.reason_code
    ? t(`assistant.reason.${chat.reason_code}`)
    : chat.reason ?? "";
  store.setChat({
    available: chat.available,
    reason: chat.available ? "" : localized,
    messagesLeft: chat.messages_left ?? 0,
  });
  return chat;
}

/** Oturum, kütüphane VE araç kaydı tamamlanınca çözülüyor. */
export const ready = (async () => {
  try {
    const session = await api.readSession();
    store.patch({ session: session.session });

    const library = await api.libraryStats();
    store.patch({ library: library.stats });

    // Asistan anahtar yoksa kapalı. Ürünün geri kalanı bundan etkilenmiyor, o
    // yüzden hata olsa bile başlatmayı düşürmüyoruz.
    try {
      await refreshChatStatus();
    } catch (error) {
      store.setChat({
        available: false,
        reason: t("assistant.statusFailed", { error: error.message }),
      });
    }

    const webmcp = await installTools({ actions, store });

    store.setStatus(
      "ok",
      webmcp.available
        ? t("status.readyWithTools", {
            takes: library.stats.takes,
            tools: webmcp.registered.length,
          })
        : t("status.readyNoTools", { takes: library.stats.takes })
    );
    return { actions, store, player, webmcp };
  } catch (error) {
    store.setStatus("error", t("status.startFailed", { error: error.message }));
    throw error;
  }
})();

// WebMCP kayıt katmanı ve konsoldan elle sürmek için.
window.__cinema = { ready, actions, store, player };
