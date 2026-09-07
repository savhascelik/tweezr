/**
 * Bağlama noktası.
 *
 * `ready` promise'i dışa açılıyor: WebMCP araç kaydı (task 4) oturum ve kütüphane
 * hazır olduktan SONRA yapılmak zorunda. Geçen projede bunu atlayınca kayıt yarışı
 * yüzünden ajan boş araç listesi görmüştü.
 */

import * as api from "./api.js";
import { createPlayer } from "./player.js";
import * as store from "./store.js";
import { createUI } from "./ui.js";

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

  async render() {
    const { timeline } = store.getState();
    if (!timeline.length) {
      store.setStatus("warn", "Kurgu boş.");
      return;
    }
    store.setStatus("busy", "Render isteniyor…");
    try {
      const result = await api.requestRender(timeline);
      store.setStatus("ok", `Render kuyruğa alındı: ${result.job_id ?? "?"}`);
      return result;
    } catch (error) {
      // Render işçisi henüz devrede değil ve kredi harcamıyor; mesaj bunu söylüyor
      store.setStatus("warn", error.message);
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

/** Oturum ve kütüphane hazır olunca çözülüyor. Araç kaydı bunu bekliyor. */
export const ready = (async () => {
  try {
    const session = await api.readSession();
    store.patch({ session: session.session });

    const library = await api.libraryStats();
    store.patch({ library: library.stats });

    store.setStatus(
      "ok",
      `Hazır. ${library.stats.takes} take yüklü, arama ve önizleme bedava.`
    );
  } catch (error) {
    store.setStatus(
      "error",
      `Sunucuya bağlanılamadı: ${error.message}. ClickHouse ayakta mı?`
    );
    throw error;
  }
  return { actions, store, player };
})();

// WebMCP kayıt katmanı (task 4) bunları kullanacak.
window.__cinema = { ready, actions, store, player };
