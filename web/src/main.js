/**
 * The wiring.
 *
 * The `ready` promise is exported because WebMCP registration has to happen AFTER the
 * session and the library are ready. Skipping that on the previous project let a
 * registration race show the agent an empty tool list. The tool descriptions also carry
 * the credit balance, so the right description cannot be produced without the session.
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

  /** Order is the edit, and it costs nothing because nothing has been rendered. */
  reorder(from, to) {
    const timeline = store.reorderTimeline(from, to);
    store.setStatus("ok", t("status.reordered", { count: timeline.length }));
    return timeline;
  },

  /**
   * Replaces one block with a different take of the same line, in place.
   *
   * In place matters: the editor chose the order, and swapping a delivery is not a
   * reason to lose it.
   */
  swap(index, candidate) {
    const { timeline } = store.getState();
    if (!timeline[index]) return timeline;
    const next = [...timeline];
    next[index] = candidate;
    const applied = store.setTimeline(next);
    store.setStatus(
      "ok",
      t("status.swapped", { take: candidate.take_id, position: index + 1 })
    );
    return applied;
  },

  /**
   * Jumps to a segment of the cut.
   *
   * The whole timeline is handed to the player with a start index rather than a slice,
   * so the index that comes back still refers to the same list the store holds.
   */
  jump(index) {
    const { timeline } = store.getState();
    if (!timeline.length) return;
    const target = Math.min(Math.max(index, 0), timeline.length - 1);
    store.setPlayback({ playing: true, index: target, offsetMs: 0 });
    player.play(timeline, target);
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
   * The in-page assistant. The candidates the agent found and the proposal it placed go
   * through the SAME store operations the WebMCP tools use — so both entry points change
   * one timeline, and the human never has to tell which was used.
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
   * The single irreversible step, and the only one that spends credit.
   *
   * Both the agent and the human arrive here, but in both cases the approval is the
   * human's. When the agent calls it, the tool's promise waits on the dialog — the HITL
   * gate made literal.
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
  onReorder: actions.reorder,
  onSwap: actions.swap,
  onClearTimeline: actions.clear,
  onPlay: actions.play,
  onStop: actions.stop,
  onJump: actions.jump,
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

// A locale change refreshes all the text. There is no second code path: because `render`
// also writes the static labels, it proceeds exactly like a state change.
onLocaleChange(() => {
  ui.render(store.getState());
  refreshChatStatus().catch(() => {});
});

ui.render(store.getState());

/**
 * Localises why the assistant is off.
 *
 * The server returns a machine-readable `reason_code` and the client produces the text —
 * the server does not know the reader's language and has no business guessing. For a code
 * we do not recognise we fall back to the server's English text rather than showing
 * nothing.
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

/** Resolves once the session, the library AND tool registration are all done. */
export const ready = (async () => {
  try {
    const session = await api.readSession();
    store.patch({ session: session.session });

    const library = await api.libraryStats();
    store.patch({ library: library.stats });

    // The assistant is off without a key. The rest of the product does not depend on it,
    // so even a failure here must not bring startup down.
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

// For the WebMCP registration layer, and for driving this by hand from the console.
window.__cinema = { ready, actions, store, player };
